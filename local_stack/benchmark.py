"""Bounded synthetic llama.cpp measurements under the caller's runtime admission."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import socket
import threading
import time

from .benchmark_fixtures import vision_quick_cases
from .registry import StackError, atomic_json, normalize_context, resource_limits


_DEADLINE_SECONDS = 120
_SAMPLE_SECONDS = 2
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_EVENT_BYTES = 65536
_QUALITY_ANSWER = "COPPER LANTERN"
_QUALITY_PROMPT = "Reply with exactly COPPER LANTERN and nothing else. Do not use quotes or punctuation."
_CONTEXT_HEADROOM = 16
_INKLING_VISION_ALIAS = "inkling2-mini-long"
_INKLING_VISION_ARTIFACT = Path("D:/MODELS/Inkling-2-Mini-abliterated-Mix-12G.gguf")
_INKLING_VISION_PROJECTOR = Path("D:/MODELS/mmproj-Inkling-2-Mini-Q8_0.gguf")
BENCHMARK_PROFILES = frozenset({"quick", "near-context", "vision-quick"})
_LAUNCH_FIELDS = (
    "path", "engine", "mmproj", "batch_size", "ubatch_size", "kv_cache", "gpu_layers",
    "fit_target_mib", "flash_attention", "load_mode", "image_min_tokens", "extra_args",
    "reasoning", "reasoning_effort", "reasoning_budget", "max_output_tokens", "temperature",
    "top_p", "top_k", "min_p",
)


def _require_inkling_vision(model):
    try:
        if (model["id"] == _INKLING_VISION_ALIAS and "image" in model["input"]
                and Path(model["path"]).resolve() == _INKLING_VISION_ARTIFACT.resolve()
                and Path(model["mmproj"]).resolve() == _INKLING_VISION_PROJECTOR.resolve()):
            return
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        pass
    raise StackError("Vision-quick requires the registered Inkling Mix-12G image profile and its reviewed projector.")


def _workload(model, context, profile):
    if not isinstance(profile, str) or profile not in BENCHMARK_PROFILES:
        raise StackError("Unknown benchmark profile; choose quick, near-context or vision-quick.")
    near = profile == "near-context"
    if near and context > 8192:
        raise StackError("Near-context benchmark requires a configured context of at most 8192 tokens.")
    if profile == "vision-quick":
        _require_inkling_vision(model)
    requested = context * 7 // 8 if near else 1024
    output = min(128 if near else 64, model["max_output_tokens"])
    return min(requested, context - output - _CONTEXT_HEADROOM), output, min(256, model["max_output_tokens"])


def _corpus():
    return "".join(
        f"def total_inventory_{i:04d}(entries):\n"
        f"    # Fictional warehouse inventory exercise, station {i:04d}.\n"
        f"    selected = [row for row in entries if row['aisle'] == {i % 31}]\n"
        f"    units = sum(row.get('units', 0) for row in selected)\n"
        f"    return {{'station': {i}, 'units': units, 'rows': len(selected)}}\n\n"
        for i in range(256)
    )


def _require_ac():
    # This launcher targets Windows. Unknown power state is not treated as AC.
    class PowerStatus(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ubyte) for name in ("ac", "flags", "percent", "saver")] + [
            ("life", ctypes.c_uint32), ("full_life", ctypes.c_uint32),
        ]

    status = PowerStatus()
    if os.name != "nt" or not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise StackError("Benchmark needs a verified AC power state.", 503)
    if status.ac != 1 or status.saver != 0:
        raise StackError("Connect AC power and disable battery saver before benchmarking.", 503)


def _finite(value, minimum=0):
    return type(value) in (int, float) and minimum <= value < math.inf


def _benchmark_reserves(resources, model):
    limits = resource_limits(resources, model)
    ram, vram = limits["minimum_available_ram_gib"], limits["minimum_free_vram_mib"]
    if not limits["experimental"]:
        # A normal benchmark retains the existing production minimums.
        ram, vram = max(2, ram), max(2048, vram)
    return ram, vram, limits["experimental"]


class _BenchmarkGuard:
    """Watch elapsed time and reserve independently of blocked native stream reads."""

    def __init__(self, runtime, model):
        self.ram_floor, self.vram_floor, self.experimental = _benchmark_reserves(
            runtime.base_registry.get("resources", {}), model,
        )
        self.resources = runtime.resources
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.connection = None
        self.failure = None
        self.samples = None
        self.sampler = threading.Thread(target=self._watch, daemon=True)
        self.timer = threading.Timer(_DEADLINE_SECONDS, self._cancel, ("Benchmark deadline exceeded.", 504))
        self.timer.daemon = True

    def __enter__(self):
        self._sample()
        self.timer.start()
        self.sampler.start()
        return self

    def __exit__(self, exc_type, *_):
        self.stopped.set()
        self.timer.cancel()
        self.timer.join()
        self.sampler.join()
        self.check()
        if exc_type is None:
            self._sample()

    def _sample(self):
        _require_ac()
        try:
            sample = self.resources()
            ram = sample["available_ram_gib"]
            vram = sample["free_vram_mib"]
            if not _finite(ram) or type(vram) is not int or vram < 0:
                raise ValueError
        except Exception:
            raise StackError("Benchmark RAM/VRAM telemetry unavailable.", 503) from None
        if ram < self.ram_floor or vram < self.vram_floor:
            policy = "selected experimental reserve" if self.experimental else "production reserve"
            raise StackError(f"Benchmark stopped: available RAM or free VRAM fell below the {policy}.", 503)
        if self.samples is None:
            self.samples = {"minimum_available_ram_gib": ram, "minimum_free_vram_mib": vram, "sample_count": 1}
            if self.experimental:
                self.samples["reserve"] = {"source": "experimental", "ram_floor_gib": self.ram_floor, "vram_floor_mib": self.vram_floor}
        else:
            self.samples["minimum_available_ram_gib"] = min(self.samples["minimum_available_ram_gib"], ram)
            self.samples["minimum_free_vram_mib"] = min(self.samples["minimum_free_vram_mib"], vram)
            self.samples["sample_count"] += 1

    def _watch(self):
        while not self.stopped.wait(_SAMPLE_SECONDS):
            try:
                self._sample()
            except StackError as error:
                self._cancel(str(error), error.status)
                return
            except Exception:
                self._cancel("Benchmark power or memory telemetry unavailable.", 503)
                return

    def _cancel(self, message, status):
        with self.lock:
            if self.failure is None:
                self.failure = StackError(message, status)
            if self.connection is not None:
                self.connection.abort()

    def check(self):
        with self.lock:
            if self.failure is not None:
                raise self.failure

    @contextmanager
    def request(self, port, key, path, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with self.lock:
            if self.failure is not None:
                raise self.failure
            connection = self.connection = _LoopbackConnection(port)
        try:
            # HTTPConnection has no proxy discovery, redirect handling, or caller-supplied URL.
            connection.request("POST", path, body=body, headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json",
            })
            with connection.getresponse() as response:
                if response.status != 200:
                    raise StackError(f"Native benchmark request failed (HTTP {response.status}).", 502)
                yield response
        except (OSError, http.client.HTTPException, ValueError):
            self.check()
            raise StackError("Native benchmark response was invalid or interrupted.", 502) from None
        finally:
            connection.close()
            with self.lock:
                self.connection = None


class _LoopbackConnection(http.client.HTTPConnection):
    """Keep the transport reachable for shutdown even when HTTPResponse owns it."""

    def __init__(self, port):
        super().__init__("127.0.0.1", port=port, timeout=_DEADLINE_SECONDS)
        self.transport = None
        self.cancelled = threading.Event()

    def connect(self):
        if self.cancelled.is_set():
            raise OSError("Cancelled before connection")
        super().connect()
        self.transport = self.sock
        if self.cancelled.is_set():
            self.abort()
            raise OSError("Cancelled on connection")

    def abort(self):
        self.cancelled.set()
        if self.transport is not None:
            try:
                self.transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _bounded_json(backend, path, body):
    with backend(path, body) as response:
        raw = response.read(_MAX_JSON_BYTES + 1)
        if len(raw) > _MAX_JSON_BYTES:
            raise StackError("Native benchmark response exceeded its evidence limit.", 502)
        return json.loads(raw)


def _tokens(backend, text):
    payload = _bounded_json(backend, "/tokenize", {"content": text, "add_special": False, "parse_special": False})
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, list) or not tokens or any(type(token) is not int or token < 0 for token in tokens):
        raise StackError("Native tokenizer did not return token IDs.", 502)
    return tokens


def _performance_completion(backend, tokens, output):
    payload = {"prompt": tokens, "n_predict": output, "cache_prompt": False, "stream": True,
               "temperature": 0, "seed": 42, "ignore_eos": True}
    start = time.monotonic()
    first_content = None
    terminal = None
    total_bytes = 0
    with backend("/completion", payload) as response:
        while raw := response.readline(_MAX_EVENT_BYTES + 1):
            total_bytes += len(raw)
            if len(raw) > _MAX_EVENT_BYTES or total_bytes > _MAX_JSON_BYTES:
                raise StackError("Native benchmark stream exceeded its evidence limit.", 502)
            if not raw.startswith(b"data:"):
                continue
            data = raw[5:].strip()
            if data == b"[DONE]":
                break
            event = json.loads(data)
            if not isinstance(event, dict) or "error" in event:
                raise StackError("Native benchmark stream reported an error.", 502)
            content = event.get("content")
            if first_content is None and isinstance(content, str) and content:
                first_content = (time.monotonic() - start) * 1000
            if event.get("stop") is True:
                terminal = event
                break
    wall_ms = (time.monotonic() - start) * 1000
    timings = terminal.get("timings") if terminal else None
    if not isinstance(timings, dict):
        raise StackError("Native benchmark ended without final timing evidence.", 502)
    prompt_n, output_n = timings.get("prompt_n"), timings.get("predicted_n")
    if (type(prompt_n) is not int or prompt_n != len(tokens)
            or type(output_n) is not int or not 0 <= output_n <= output
            or timings.get("cache_n", 0) != 0):
        raise StackError("Native benchmark counts did not match the uncached workload.", 502)
    fields = {"prefill_ms": "prompt_ms", "prefill_tps": "prompt_per_second",
              "decode_ms": "predicted_ms", "decode_tps": "predicted_per_second"}
    metrics = {name: timings.get(field) for name, field in fields.items()}
    if (output_n != output or first_content is None
            or any(not _finite(value) or value <= 0 for value in metrics.values())):
        raise StackError("Native benchmark did not produce complete performance evidence.", 502)
    return {"prompt_tokens": prompt_n, "output_tokens": output_n, **metrics,
            "ttft_ms": round(first_content, 3), "wall_ms": round(wall_ms, 3)}


def _chat_canary(backend, model, context, output, content, *, minimal_reasoning=False):
    # The guarded native chat path applies the model template and stops at natural EOS.
    request = {"model": model, "messages": [{"role": "user", "content": content}],
               "max_tokens": output, "temperature": 0, "seed": 42, "stream": False,
               "cache_prompt": False, "ignore_eos": False, "store": False}
    if minimal_reasoning:
        request["chat_template_kwargs"] = {"reasoning_effort": "none"}
    payload = _bounded_json(backend, "/v1/chat/completions", request)
    if not isinstance(payload, dict) or "error" in payload:
        raise StackError("Native chat canary returned an invalid response.", 502)
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise StackError("Native chat canary did not report token usage.", 502)
    prompt_n, output_n, total_n = (usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if (any(type(value) is not int for value in (prompt_n, output_n, total_n))
            or prompt_n <= 0 or not 0 <= output_n <= output or total_n != prompt_n + output_n
            or prompt_n + output + _CONTEXT_HEADROOM > context):
        raise StackError("Native chat canary usage exceeded or did not match its token budget.", 502)
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise StackError("Native chat canary did not return one choice.", 502)
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise StackError("Native chat canary did not return an assistant message.", 502)
    # Usage includes reasoning; only final assistant content participates in scoring.
    finish = choice.get("finish_reason")
    if finish not in ("stop", "length", "tool_calls", "content_filter"):
        finish = "unknown"
    if message.get("tool_calls"):
        finish = "tool_calls"
    return finish, message.get("content"), prompt_n, output_n


def _quality_completion(backend, model, context, output):
    finish, content, prompt_n, output_n = _chat_canary(backend, model, context, output, _QUALITY_PROMPT)
    passed = finish == "stop" and output_n > 0 and isinstance(content, str) and content.strip() == _QUALITY_ANSWER
    return {"passed": passed, "prompt_tokens": prompt_n, "output_tokens": output_n}


def _vision_quick_quality(backend, model, context, output):
    cases = []
    for fixture in vision_quick_cases():
        started = time.monotonic()
        cap = min(output, 128) if fixture.modality == "image" else output
        finish, content, prompt_n, output_n = _chat_canary(
            backend, model, context, cap, fixture.content(), minimal_reasoning=True,
        )
        wall_ms = round((time.monotonic() - started) * 1000, 3)
        cases.append({"case": fixture.name, "modality": fixture.modality,
                      "passed": finish == "stop" and output_n > 0 and fixture.matches(content),
                      "finish": finish, "prompt_tokens": prompt_n, "output_tokens": output_n, "wall_ms": wall_ms})
    count = sum(case["passed"] for case in cases)
    return {"passed": count == len(cases), "passed_count": count, "case_count": len(cases), "cases": cases}


def _fingerprint(model, context):
    launch = {field: model.get(field) for field in _LAUNCH_FIELDS}
    if model.get("mmproj") and model.get("mmproj_gpu") is False:
        launch["mmproj_gpu"] = False
    if model.get("experimental_resources") is not None:
        launch["experimental_resources"] = model["experimental_resources"]
    for field in ("path", "engine", "mmproj"):
        if launch[field]:
            stat = Path(launch[field]).stat()
            launch[field] = [launch[field], stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    encoded = json.dumps([model["id"], context, launch], sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_path(state_dir, model):
    model_key = hashlib.sha256(model["id"].encode("utf-8")).hexdigest()
    return Path(state_dir) / "benchmarks" / (model_key + ".json")


def _validated_context(model, context):
    value = model["context"] if context is None else context
    if type(value) is not int:
        raise StackError("Context must be an integer.")
    context = normalize_context(value)
    if not model["minimum_context"] <= context <= model["maximum_context"] or context <= model["max_output_tokens"]:
        raise StackError("Context is outside the profile range or leaves no input budget.")
    return context


def benchmark_preview(model, context=None, *, state_dir=None):
    """Return a historical observation or configured startup allowance, never a fit prediction."""
    context = _validated_context(model, context)
    configured = model.get("estimated_vram_mib")
    if type(configured) is not int or configured <= 0:
        configured = None
    preview = {"source": "configured" if configured else "unavailable", "context": context,
               "estimated_vram_mib": configured, "measured_minimum_free_vram_mib": None,
               "measured_minimum_available_ram_gib": None,
               "note": ("Configured startup VRAM allowance, not a context-specific memory estimate or fit guarantee."
                        if configured else "No matching memory measurement or configured startup allowance.")}
    if state_dir is not None:
        try:
            with _evidence_path(state_dir, model).open("rb") as file:
                evidence = json.loads(file.read(_MAX_EVENT_BYTES + 1))
            result = evidence["result"]
            sample = result["resources"]
            ram, vram = sample["minimum_available_ram_gib"], sample["minimum_free_vram_mib"]
            ram_floor, vram_floor, experimental = _benchmark_reserves({}, model)
            if (evidence["version"] == 1 and evidence["launch_fingerprint"] == _fingerprint(model, context)
                    and result["source"] == "measured" and result["status"] == "ok"
                    and result["model"] == model["id"] and result["context"] == context
                    and _finite(ram, ram_floor) and type(vram) is int and vram >= vram_floor):
                policy = " under the selected experimental reserve" if experimental else ""
                preview.update(source="measured", measured_minimum_free_vram_mib=vram,
                               measured_minimum_available_ram_gib=ram,
                               note=f"Observed during the last matching synthetic benchmark{policy}; not current usage or a fit guarantee.")
        except (OSError, ValueError, KeyError, TypeError):
            pass  # No usable measurement; the configured estimate remains clearly labelled.
    return preview


def run_benchmark(runtime, model, context=None, profile="quick"):
    """Run on the owned loopback worker; caller holds exclusive admission until return.

    Leaves the selected model loaded. Only successful complete measurements are saved, even
    when the separately reported synthetic quality canary fails. No generated text is saved.
    """
    config = runtime.find_model(model)
    context = _validated_context(config, context)
    prompt_n, output_n, quality_n = _workload(config, context, profile)
    key = os.environ.get("LLAMA_CPP_API_KEY")
    port = runtime.backend_port
    if not key or type(port) is not int or not 1 <= port <= 65535:
        raise StackError("Configured authenticated loopback backend is required for benchmarking.", 503)
    _require_ac()
    # Runtime applies the same model ownership, readiness and startup constraints as inference.
    config = runtime.ensure_loaded(model, context)
    try:
        fingerprint = _fingerprint(config, context)
    except OSError:
        fingerprint = None  # Can measure a resident worker but cannot identify its files for reuse.
    with _BenchmarkGuard(runtime, config) as guard:
        def backend(path, payload):
            return guard.request(port, key, path, payload)

        tokens = _tokens(backend, _corpus())
        if len(tokens) < prompt_n:
            raise StackError("Synthetic corpus did not reach the fixed token target.", 502)
        performance = _performance_completion(backend, tokens[:prompt_n], output_n)
        quality = (_vision_quick_quality(backend, model, context, quality_n) if profile == "vision-quick"
                   else _quality_completion(backend, model, context, quality_n))
    result = {"status": "ok", "source": "measured", "model": model, "context": context,
              "profile": profile, "measured_at": datetime.now(timezone.utc).isoformat(),
              "performance": performance, "quality": quality, "resources": guard.samples,
              "persisted": False}
    if fingerprint is not None:
        try:
            saved = {**result, "persisted": True}
            atomic_json(_evidence_path(runtime.state_dir, config),
                        {"version": 1, "launch_fingerprint": fingerprint, "result": saved})
            return saved
        except OSError:
            pass  # The current measurement still succeeded; persisted explicitly remains false.
    return result
