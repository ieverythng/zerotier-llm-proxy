"""Metadata-only request flow and passive, owned-backend metrics.

No prompt, output text, request headers, credentials, URLs or raw metrics are
retained. A gateway stage observes transport/lifecycle code, not GPU execution.
Proxy stages are explicitly inferred from the configured route. Backend activity
comes only from authenticated metrics of a still-owned process.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.request
import uuid

from .runtime import HTTP

KINDS = {"inference", "benchmark"}
STAGES = {"ingress", "validated", "load_or_validate", "forward_wait", "response_headers", "streaming", "first_content", "benchmark", "completed", "failed", "rejected", "cancelled"}
OUTCOMES = {"completed", "failed", "rejected", "cancelled"}
ERROR_KINDS = {None, "authorization", "validation", "busy", "stopping_or_unavailable", "upstream_http", "upstream_io", "operation_error", "local_disconnect", "internal"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class RequestFlow:
    """Generation-scoped counters. Active means admitted, not queued HTTP work."""
    def __init__(self, generation=None, *, direct=False, clock=time.monotonic, utc=utc_now):
        self.generation = generation or uuid.uuid4().hex
        self.direct, self.clock, self.utc = direct, clock, utc
        self.lock = threading.RLock()
        self.counters = {name: 0 for name in ("ingress", "accepted", "active", "completed", "failed", "rejected", "cancelled")}
        self.records = {}
        self.last = None
        self.sequence = 0

    def begin(self, kind):
        if kind not in KINDS:
            raise ValueError("Unknown metadata request kind")
        with self.lock:
            self.sequence += 1
            self.counters["ingress"] += 1
            now = self.clock()
            record = {"id": f"req-{self.sequence}", "model": None, "kind": kind, "stage": "ingress", "source": "observed", "streaming": False, "first_content_ms": None, "bytes_forwarded": 0, "event_seq": 1, "_started": now, "_stage_started": now, "_accepted": False, "_finished": False}
            # Single inference admission limits normal use to one. Bound even
            # unauthenticated/slow-body metadata independently of HTTP threads.
            if len(self.records) < 64:
                self.records[record["id"]] = record
            return record

    def describe(self, record, model, streaming=False):
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", model):
            raise ValueError("Only a validated registry id may enter telemetry")
        with self.lock:
            record["model"] = model
            record["streaming"] = bool(streaming)
            self.stage(record, "validated")

    def accept(self, record):
        with self.lock:
            if not record["_accepted"] and not record["_finished"]:
                if record['id'] not in self.records:
                    pending = next((key for key, value in self.records.items() if not value['_accepted']), None)
                    if pending is not None:
                        self.records.pop(pending)
                    if len(self.records) < 64:
                        self.records[record['id']] = record
                record["_accepted"] = True
                self.counters["accepted"] += 1
                self.counters["active"] += 1

    def stage(self, record, stage):
        if stage not in STAGES:
            raise ValueError("Unknown metadata stage")
        with self.lock:
            if not record["_finished"] and record["stage"] != stage:
                record.update(stage=stage, _stage_started=self.clock(), event_seq=record["event_seq"] + 1)

    def forwarded(self, record, count):
        with self.lock:
            if type(count) is not int or count < 0:
                raise ValueError("Byte count must be nonnegative")
            if not record["_finished"]:
                record["bytes_forwarded"] += count
                record["event_seq"] += 1
                if record["streaming"]:
                    self.stage(record, "streaming")

    def first_content(self, record):
        with self.lock:
            if record["first_content_ms"] is None and not record["_finished"]:
                record["first_content_ms"] = round(max(0, self.clock() - record["_started"]) * 1000, 3)
                self.stage(record, "first_content")

    def _public(self, record, now=None):
        now = self.clock() if now is None else now
        fields = {"id", "model", "kind", "stage", "source", "streaming", "first_content_ms", "bytes_forwarded", "event_seq"}
        result = {key: record[key] for key in fields}
        result["elapsed_ms"] = round(max(0, now - record["_started"]) * 1000, 3)
        result["stage_elapsed_ms"] = round(max(0, now - record["_stage_started"]) * 1000, 3)
        return result

    def finish(self, record, outcome, error_kind=None):
        if outcome not in OUTCOMES or error_kind not in ERROR_KINDS:
            raise ValueError("Unknown metadata outcome")
        with self.lock:
            if record["_finished"]:
                return
            self.stage(record, outcome)
            record["_finished"] = True
            self.counters[outcome] += 1
            if record["_accepted"]:
                self.counters["active"] -= 1
            public = self._public(record)
            public.update(outcome=outcome, error_kind=error_kind, duration_ms=public["elapsed_ms"])
            self.last = public
            self.records.pop(record["id"], None)

    def snapshot(self, backend=None):
        with self.lock:
            active = [self._public(record) for record in self.records.values() if record["_accepted"] and not record["_finished"]]
            forwarded = any(record["kind"] == "inference" and record["stage"] in {"forward_wait", "response_headers", "streaming", "first_content"} for record in active)
            bypassed = self.direct or bool(active) and all(record['kind'] == 'benchmark' for record in active)
            proxy = {"state": "bypassed" if bypassed else "in_flight" if forwarded else "unknown", "source": "configured" if bypassed else "inferred" if forwarded else "unknown"}
            return {"version": 1, "generation": self.generation, "route": "direct" if self.direct else "headroom_litellm", "counter_scope": "inference_and_benchmark_posts", "completion_scope": "gateway_transport_not_task_success", "observed_at": self.utc(), "counters": dict(self.counters), "active": active, "last": deepcopy(self.last), "nodes": {"gateway": {"state": "active" if self.counters["active"] else "idle", "source": "observed"}, "headroom": dict(proxy), "litellm": dict(proxy)}, "backend": deepcopy(backend or unknown_backend())}


def unknown_backend():
    return {"state": "unknown", "source": "authenticated_metrics", "correlation": "process_not_request", "observed_at": None, "stale": True, "epoch": None, "processing": None, "prompt_tokens_total": None, "generated_tokens_total": None}


_METRICS = {"llamacpp:requests_processing": "processing", "llamacpp:prompt_tokens_total": "prompt_tokens_total", "llamacpp:tokens_predicted_total": "generated_tokens_total"}


def parse_backend_metrics(text):
    """Allowlist finite unlabeled scalar samples, never export raw Prometheus text."""
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(llamacpp:[a-z_]+)\s+([+\-0-9.eE]+)(?:\s+[0-9]+)?\s*", line)
        if match and match[1] in _METRICS:
            value = float(match[2])
            if math.isfinite(value) and value >= 0:
                name = _METRICS[match[1]]
                if name in result:
                    raise ValueError("Duplicate scalar metric")
                result[name] = int(value) if value.is_integer() else value
    return result


class OwnedBackendMetrics:
    """At most one 0.4-second, 64-KiB passive query every two seconds.

    refresh() is background-only; snapshots never wait for HTTP or hold runtime
    admission. Sampling does not infer decode/prefill from GPU utilization.
    """
    def __init__(self, runtime, *, opener=HTTP, clock=time.monotonic, utc=utc_now, interval=2.0, timeout=0.4):
        self.runtime, self.opener = runtime, opener
        self.clock, self.utc, self.interval, self.timeout = clock, utc, interval, timeout
        self.lock = threading.Lock()
        self.pending = False
        self.next_poll = 0.0
        self.sample_time = None
        self.sample = unknown_backend()
        self.epoch = None

    def _owned(self):
        record = self.runtime.record
        if not record or not record.get("ready") or self.runtime.identify(record["pid"]) != record["identity"]:
            return None
        return {"pid": record["pid"], "identity": deepcopy(record["identity"]), "model": record["model"], "context": record["context"]}

    @staticmethod
    def _epoch(record):
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def maybe_refresh(self):
        owned = self._owned()
        with self.lock:
            if owned is None:
                self.sample = unknown_backend()
                self.sample_time, self.epoch = None, None
                return
            epoch = self._epoch(owned)
            if epoch != self.epoch:
                self.sample = {**unknown_backend(), "epoch": epoch}
                self.sample_time, self.epoch = None, epoch
                self.next_poll = 0
            if self.pending or self.clock() < self.next_poll:
                return
            self.pending = True
            self.next_poll = self.clock() + self.interval
        threading.Thread(target=self._refresh, args=(owned, epoch), daemon=True, name="owned-backend-metrics").start()

    def _refresh(self, owned, epoch):
        try:
            key = os.environ.get("LLAMA_CPP_API_KEY")
            if not key:
                raise ValueError("No backend authentication configured")
            request = urllib.request.Request(f"http://127.0.0.1:{self.runtime.backend_port}/metrics", headers={"Authorization": "Bearer " + key})
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValueError("Non-success metric response")
                payload = response.read(65537)
            if len(payload) > 65536:
                raise ValueError("Oversized metric response")
            parsed = parse_backend_metrics(payload.decode("utf-8"))
            if "processing" not in parsed or self._owned() != owned:
                raise ValueError("Metrics unavailable or model ownership changed")
            with self.lock:
                if self.epoch == epoch:
                    self.sample = {**unknown_backend(), **parsed, "state": "processing" if parsed["processing"] > 0 else "idle", "observed_at": self.utc(), "stale": False, "epoch": epoch}
                    self.sample_time = self.clock()
        except Exception:
            with self.lock:
                if self.epoch == epoch:
                    self.sample["stale"] = True
                    self.sample["state"] = "unknown"
        finally:
            with self.lock:
                self.pending = False

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.sample)
            if self.sample_time is None or self.clock() - self.sample_time > max(5, self.interval * 2.5):
                result.update(stale=True, state="unknown")
            return result


class ContentPresenceProbe:
    """Transient bounded parsing to observe first text content, never retaining it.

    Buffers are local to one transport read loop and cleared when parsed/closed;
    no content enters RequestFlow records, snapshots, files or logs. Oversized
    events stay unobserved rather than guessing from bytes or reasoning tokens.
    """
    def __init__(self, streaming):
        self.streaming = streaming
        self.buffer = bytearray()
        self.seen = False
        self.disabled = False

    @staticmethod
    def _has_text(event, streaming):
        try:
            choices = event.get("choices", [])
            for choice in choices:
                value = choice.get("delta" if streaming else "message", {}).get("content")
                if isinstance(value, str) and value:
                    return True
            return False
        except (AttributeError, TypeError):
            return False

    def feed(self, chunk):
        if self.seen or self.disabled:
            return False
        self.buffer.extend(chunk)
        limit = 16384 if self.streaming else 65536
        if len(self.buffer) > limit:
            self.buffer.clear()
            self.disabled = True
            return False
        if not self.streaming:
            return False
        while b"\n" in self.buffer:
            line, _, remaining = self.buffer.partition(b"\n")
            self.buffer = bytearray(remaining)
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except (ValueError, UnicodeError):
                continue
            if self._has_text(event, True):
                self.buffer.clear()
                self.seen = True
                return True
        return False

    def close(self):
        self.buffer.clear()
        self.disabled = True

    def finish(self):
        found = False
        if not self.streaming and not self.disabled and not self.seen:
            try:
                found = self._has_text(json.loads(self.buffer), False)
            except (ValueError, UnicodeError):
                pass
        self.buffer.clear()
        self.seen = self.seen or found
        return found
