"""Keyboard-first terminal controls for the single-active-model gateway.

The gateway owns persistence, process lifetime, admission and readiness.  This
module only renders its state and submits explicit user requests; quitting never
stops inference.  Rich is imported lazily so pure helpers remain dependency-free.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from concurrent.futures import Future
from threading import Thread
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .resource_history import COLORS, METRICS, ResourceHistory, render_resource_history, sparkline, utc_timestamp
from .registry import StackError

DEFAULT_URL = "http://127.0.0.1:4141"
INKLING_CONTEXT_VARIANTS = ("inkling2-mini", "inkling2-mini-100k", "inkling2-mini-long")
INKLING_BENCHMARK_MODELS = frozenset(INKLING_CONTEXT_VARIANTS)
INKLING_DEFAULT_ALIAS = "inkling2-mini-long"
INKLING_DEFAULT_CONTEXT = 150016
DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "config" / "models.json"
START_COMMAND = r".\scripts\windows\Start-LocalModelStack.ps1"
PREVIEW_TTL_SECONDS = 10.0
STATUS_POLL_SECONDS = 2.0
KEY_POLL_SECONDS = 0.02
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
PROFILE_KEYS = frozenset({
    "id", "name", "path", "engine", "context", "context_presets", "max_output_tokens",
    "input", "kv_cache", "batch_size", "ubatch_size", "gpu_layers", "mmproj",
    "extra_args", "reasoning_effort", "validation", "tested_prompt_tokens",
    "estimated_vram_mib", "minimum_context", "maximum_context", "profile", "notes",
    "reasoning", "load_mode", "image_min_tokens", "flash_attention", "startup_timeout_seconds",
    "fit_target_mib", "context_verified", "reasoning_budget", "temperature", "top_p", "top_k", "min_p",
})


def safe_text(value: Any, limit: int = 400) -> str:
    """Render remote/file metadata as literal text, never terminal commands."""
    value = ANSI.sub("", str(value))
    return "".join(c for c in value if c.isprintable() or c == " ")[:limit]


def safe_error(error: Any) -> str:
    text = safe_text(error)
    for name in ("LLAMA_CPP_API_KEY", "LITELLM_MASTER_KEY", "HEADROOM_PROXY_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    return text


def format_tokens(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError, OverflowError):
        return "—"


def read_registry(path: str | Path) -> list[dict[str, Any]]:
    """Read an operator catalog without ever writing it from the UI."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        rows = value.get("models", []) if isinstance(value, dict) else []
        return [dict(row) for row in rows if isinstance(row, dict) and row.get("id")]
    except (OSError, ValueError, TypeError):
        return []


def normalize_models(
    catalog: Any,
    registry: Iterable[Mapping[str, Any]] = (),
    status: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge local profile hints with authoritative gateway metadata."""
    merged = {str(row["id"]): dict(row) for row in registry if row.get("id")}
    status = status or {}
    live_rows = status.get("models", [])
    if not isinstance(live_rows, list):
        live_rows = []
    rows = catalog.get("data", []) if isinstance(catalog, dict) else catalog
    if not isinstance(rows, list):
        rows = []
    # Full runtime profiles are authoritative over the on-disk defaults.
    for row in rows + live_rows:
        if isinstance(row, dict) and row.get("id"):
            model_id = str(row["id"])
            merged.setdefault(model_id, {}).update(row)
    result = []
    for model_id, row in merged.items():
        row["id"] = model_id
        row.setdefault("name", model_id)
        context = row.get("context_window", row.get("context"))
        if row.get("context") is not None and any(
            isinstance(live, dict) and live.get("id") == model_id and "context" in live
            for live in live_rows
        ):
            context = row["context"]
        row["context"] = context
        if not row.get("validation"):
            row["validation"] = "Previously measured profile; readiness rechecked on load" if row.get("context_verified") is True else "provisional / unverified"
        result.append(row)
    return sorted(result, key=lambda row: (safe_text(row.get("name", "")).casefold(), row["id"]))


def context_presets(model: Mapping[str, Any]) -> list[int]:
    candidates = list(model.get("context_presets") or [])
    candidates.append(model.get("context", model.get("context_window")))
    values = set()
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
            if number > 0:
                values.add(number)
        except (TypeError, ValueError, OverflowError):
            continue
    return sorted(values)


def context_limit(model: Mapping[str, Any]) -> int:
    try:
        explicit = int(model.get("maximum_context", model.get("max_context", model.get("context_limit", 0))))
    except (ValueError, TypeError):
        explicit = 0
    return explicit or max(context_presets(model), default=8192)


def validate_context(value: Any, model: Mapping[str, Any]) -> int:
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ValueError("Context must be a whole number of tokens.")
    number = int(str(value).strip())
    minimum = max(512, int(model.get("minimum_context", 512)))
    if number < minimum:
        raise ValueError(f"Context must be at least {minimum:,} tokens.")
    if number <= int(model.get("max_output_tokens", 0)):
        raise ValueError("Context must exceed this profile's output budget; choose a larger context.")
    maximum = context_limit(model)
    if number > maximum:
        raise ValueError(f"This profile allows at most {maximum:,} tokens; validate a larger profile first.")
    if number % 256:
        raise ValueError("Use a multiple of 256 tokens; contexts are never silently rounded here.")
    return number


def validate_gguf(path: str | Path, *, projector: bool = False) -> Path:
    candidate = Path(str(path).strip().strip('"')).expanduser()
    if candidate.suffix.lower() != ".gguf":
        raise ValueError("Choose a .gguf file, not a directory or partial download.")
    if not candidate.is_file():
        raise ValueError("That GGUF file does not exist.")
    if not projector and "mmproj" in candidate.name.lower():
        raise ValueError("An mmproj is an image projector, not a model. Select the main model GGUF.")
    with candidate.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("The file does not have a GGUF header; finish/check the download first.")
    return candidate.resolve()


def discover_ggufs(roots: Iterable[str | Path] | None = None, limit: int = 40) -> list[Path]:
    """Bounded discovery: two directory levels, no symlink traversal or drive scan."""
    if roots is None:
        roots = (Path("D:/MODELS"), Path.home() / "Downloads")
    found: list[Path] = []
    visited = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir() or root.is_symlink():
            continue
        try:
            top = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        candidates = list(top)
        for directory in top:
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                candidates.extend(sorted(directory.iterdir(), key=lambda item: item.name.casefold())[:200])
            except OSError:
                continue
        for candidate in candidates:
            if candidate.is_symlink() or candidate.suffix.lower() != ".gguf" or "mmproj" in candidate.name.lower():
                continue
            if not candidate.is_file():
                continue
            key = str(candidate.resolve()).casefold()
            if key not in visited:
                visited.add(key)
                found.append(candidate.resolve())
                if len(found) >= limit:
                    return found
    return found


def build_registration(
    template: Mapping[str, Any],
    *,
    model_id: str,
    name: str,
    path: str | Path,
    context: Any,
    existing_ids: Iterable[str] = (),
    mmproj: str | Path | None = None,
) -> dict[str, Any]:
    """Clone an allowlisted profile; never take executable paths or raw flags."""
    model_id = model_id.strip()
    if not MODEL_ID.fullmatch(model_id):
        raise ValueError("Use 1–64 lowercase letters, numbers, dots, hyphens or underscores; start with a letter/number.")
    if model_id in set(existing_ids):
        raise ValueError("That model id is already registered; choose a new id.")
    if not str(template.get("engine", "")).strip():
        raise ValueError("The chosen profile has no approved engine; register it through the operator configuration first.")
    name = name.strip()
    if not name or len(name) > 100 or safe_text(name, 101) != name:
        raise ValueError("Choose a printable model name of 1–100 characters.")
    artifact = validate_gguf(path)
    new_model = {key: copy.deepcopy(value) for key, value in template.items() if key in PROFILE_KEYS}
    # A new artifact cannot inherit the validation evidence of its template.
    for field in (
        "context_window", "verified_context", "verified_prompt_tokens", "context_evidence",
        "evidence", "notes", "validation_status", "readiness", "active", "loaded", "tested_prompt_tokens",
    ):
        new_model.pop(field, None)
    provisional_profile = dict(template)
    provisional_profile["maximum_context"] = min(8192, context_limit(template))
    provisional_profile["max_output_tokens"] = 0  # New artifact receives a smaller initial budget below.
    selected_context = validate_context(context, provisional_profile)
    new_model["maximum_context"] = provisional_profile["maximum_context"]
    new_model.update(
        id=model_id, name=name, path=str(artifact), context=selected_context,
        context_presets=[selected_context], validation="unverified", context_verified=False,
    )
    if "estimated_vram_mib" in new_model:
        # A new quant must not inherit a smaller artifact's startup estimate.
        new_model["estimated_vram_mib"] = max(int(new_model["estimated_vram_mib"]), (artifact.stat().st_size + 1048575) // 1048576 + 2048)
    output_budget = int(new_model.get("max_output_tokens", 2048))
    new_model["max_output_tokens"] = min(output_budget, max(128, selected_context // 4))
    if "image" in (template.get("input") or []):
        if not mmproj:
            raise ValueError("This vision profile requires the new model's matching image projector.")
        new_model["mmproj"] = str(validate_gguf(mmproj, projector=True))
    else:
        new_model.pop("mmproj", None)
    return new_model


def normalized_gguf_artifact(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or Path(value).suffix.lower() != ".gguf":
        return None
    try:
        return os.path.normcase(str(Path(value).expanduser().resolve()))
    except (OSError, ValueError):
        return None


def supports_vision_quick(model: Mapping[str, Any]) -> bool:
    """Show the option only for a known native image profile; backend verifies artifacts."""
    modalities, projector = model.get("input"), model.get("mmproj")
    return (model.get("id") == INKLING_DEFAULT_ALIAS and isinstance(modalities, (list, tuple))
            and "image" in modalities and isinstance(projector, str) and Path(projector).suffix.lower() == ".gguf")


def default_inkling_profile(model: Mapping[str, Any]) -> bool:
    return (model.get("id") == INKLING_DEFAULT_ALIAS and supports_vision_quick(model)
            and all(model.get(field) == INKLING_DEFAULT_CONTEXT for field in ("context", "minimum_context", "maximum_context")))


def grouped_model_rows(models: list[dict[str, Any]], variant_id: str | None = None) -> list[dict[str, Any]]:
    """Presentation only: never merge runtime settings or widen a profile's range."""
    known = {row["id"]: row for row in models if row.get("id") in INKLING_CONTEXT_VARIANTS}
    variants = [known[key] for key in INKLING_CONTEXT_VARIANTS if key in known]
    if len(variants) < 2:
        return list(models)
    base = variants[0]
    artifact = normalized_gguf_artifact(base.get("path"))
    if artifact is None or any(artifact != normalized_gguf_artifact(row.get("path")) for row in variants[1:]):
        return list(models)
    preferred = next((row for row in variants if default_inkling_profile(row)), base)
    chosen = known.get(variant_id, preferred)
    grouped = {**chosen, "name": base.get("name", "Inkling 2 Mini"), "_variants": variants}
    rows, added = [], False
    for row in models:
        if row["id"] in known:
            if not added:
                rows.append(grouped)
                added = True
        else:
            rows.append(row)
    return rows


def model_row_ids(row: Mapping[str, Any]) -> set[str]:
    return {str(variant["id"]) for variant in row.get("_variants", [row])}


@dataclass
class ViewState:
    models: list[dict[str, Any]]
    status: dict[str, Any]
    selected_id: str | None = None
    connected: bool = False
    notice: str = "Connecting to the local gateway…"
    action: str | None = None
    filter_text: str = ""
    benchmark_result: dict[str, Any] | None = None
    flow_sampled_at: float | None = None
    inkling_variant_id: str | None = None
    resource_history: ResourceHistory = field(default_factory=ResourceHistory)
    show_resource_charts: bool = False
    scratch_advisory: dict[str, Any] | None = None

    @property
    def visible_models(self) -> list[dict[str, Any]]:
        needle = self.filter_text.casefold()
        variant = self.selected_id if self.selected_id in INKLING_CONTEXT_VARIANTS else self.inkling_variant_id
        rows = grouped_model_rows(self.models, variant)
        return [row for row in rows if any(needle in (str(item.get("name", "")) + " " + item["id"] + " " + format_tokens(item.get("context"))).casefold() for item in row.get("_variants", [row]))]

    @property
    def selected(self) -> dict[str, Any] | None:
        rows = self.visible_models
        row = next((row for row in rows if self.selected_id in model_row_ids(row)), rows[0] if rows else None)
        if row is None:
            return None
        # Return the actual source profile so local context choices update that
        # profile only. Synthetic group metadata never goes to the gateway.
        return next((model for model in self.models if model["id"] == row["id"]), row)

    @property
    def selected_variants(self) -> list[dict[str, Any]]:
        selected = self.selected
        if not selected:
            return []
        row = next(row for row in self.visible_models if selected["id"] in model_row_ids(row))
        return row.get("_variants", [selected])

    def move(self, offset: int) -> None:
        rows = self.visible_models
        if rows:
            selected = self.selected
            if selected and selected["id"] in INKLING_CONTEXT_VARIANTS:
                self.inkling_variant_id = selected["id"]
            index = next((i for i, row in enumerate(rows) if self.selected_id in model_row_ids(row)), 0)
            self.selected_id = rows[(index + offset) % len(rows)]["id"]


def metric(value: Any, suffix: str = "") -> str:
    try:
        import math
        number = float(value)
        return f"{number:,.2f}{suffix}" if math.isfinite(number) else "unavailable"
    except (ValueError, TypeError, OverflowError):
        return "unavailable"


def experimental_guard_line(model: Mapping[str, Any]) -> str | None:
    policy = model.get("experimental_resources")
    if not isinstance(policy, dict):
        return None
    free, ram = policy.get("minimum_free_vram_mib"), policy.get("minimum_available_ram_gib")
    if type(free) is not int or not 0 < free < 2048 or type(ram) not in (int, float) or not 0 < ram <= 2:
        return None
    label = f"Experimental floor: {free:,} MiB VRAM / {ram:g} GiB RAM (normal VRAM 2,048 MiB)"
    if default_inkling_profile(model):
        from .qualification_evidence import QUALIFICATIONS
        mixed = next((record for record in QUALIFICATIONS if record.scenario == "Inkling 150K mixed recall"), None)
        if mixed is not None and mixed.minimum_free_vram_mib is not None:
            label += f" · mixed test min {mixed.minimum_free_vram_mib:,.0f} MiB, not current headroom"
    return label


def evidence_lines(model: Mapping[str, Any], result: Mapping[str, Any] | None = None) -> list[str]:
    """Keep startup estimates, historical observations and live telemetry distinct."""
    estimate = model.get("estimated_vram_mib")
    lines = ["ESTIMATED startup VRAM allowance: " + (metric(float(estimate) / 1024, " GiB") if isinstance(estimate, (int, float)) and not isinstance(estimate, bool) else "unavailable")]
    if guard := experimental_guard_line(model):
        lines.insert(0, guard)
    exact = result and result.get("source") == "measured" and result.get("model") == model.get("id") and result.get("context") == model.get("context")
    if exact:
        resources = result.get("resources") or {}
        free = resources.get("minimum_free_vram_mib")
        lines.append("MEASURED minimum free: VRAM " + (metric(float(free) / 1024, " GiB") if isinstance(free, (int, float)) else "unavailable") + " / RAM " + metric(resources.get("minimum_available_ram_gib"), " GiB"))
        lines.append("Historical observation at this profile/context; not a fit guarantee.")
    else:
        preview = model.get("benchmark_preview") or {}
        if preview.get("source") == "measured" and preview.get("context") == model.get("context"):
            free = preview.get("measured_minimum_free_vram_mib")
            lines.append("MEASURED historical minimum free: VRAM " + (metric(float(free) / 1024, " GiB") if isinstance(free, (int, float)) else "unavailable") + " / RAM " + metric(preview.get("measured_minimum_available_ram_gib"), " GiB"))
        else:
            lines.append("MEASURED memory at selected context: unavailable")
    return lines


def benchmark_lines(result: Mapping[str, Any] | None) -> list[str]:
    if not result:
        return ["No measured benchmark in this terminal session.", "B starts an exclusive, bounded Inkling benchmark; it never tunes Saluki."]
    performance = result.get("performance") or {}
    quality = result.get("quality") or {}
    lines = [
        "MEASURED · " + safe_text(result.get("model", "unknown")) + " · " + format_tokens(result.get("context")) + " ctx · " + safe_text(result.get("measured_at", "time unavailable")),
        "Input " + format_tokens(performance.get("prompt_tokens")) + " / output " + format_tokens(performance.get("output_tokens")) + " tokens · quality " + ("PASS" if quality.get("passed") is True else "not passed/unknown"),
        "Prefill " + metric(performance.get("prefill_tps"), " tok/s") + " · decode " + metric(performance.get("decode_tps"), " tok/s") + " · TTFT " + metric(performance.get("ttft_ms"), " ms"),
    ]
    if result.get("profile") == "vision-quick" and isinstance(quality.get("cases"), list):
        cases = []
        for row in quality["cases"][:12]:
            if not isinstance(row, dict) or row.get("modality") not in {"image", "text"}:
                continue
            cases.append(f"{safe_text(row.get('case', 'case'), 48)} · {row['modality']} · "
                         f"{'PASS' if row.get('passed') is True else 'not passed'} · "
                         f"{format_tokens(row.get('prompt_tokens'))} in/{format_tokens(row.get('output_tokens'))} out · "
                         f"{metric(row.get('wall_ms'), ' ms')} wall")
        if cases:
            lines.extend(cases)
            lines.append("Native vision quick · " + format_tokens(quality.get("passed_count")) + "/" + format_tokens(quality.get("case_count")) + " synthetic cases passed · E for details")
    return lines


FLOW_FRESH_SECONDS = 6.0
FLOW_STAGE_LABELS = {
    "ingress": "ingress", "validated": "validated", "load_or_validate": "load/validate",
    "forward_wait": "upstream wait", "response_headers": "headers received",
    "streaming": "stream bytes", "first_content": "answer text", "benchmark": "benchmark",
}
FLOW_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def flow_count(value: Any) -> str:
    """Unknown is not zero; reject booleans and invalid telemetry scalars."""
    return f"{value:,}" if type(value) is int and value >= 0 else "—"


def request_flow_view(state: ViewState, now: float | None = None) -> dict[str, Any]:
    """Translate observed metadata into honest display states, never inferred work."""
    now = time.monotonic() if now is None else now
    raw = state.status.get("request_flow")
    flow = raw if isinstance(raw, dict) and raw.get("version") == 1 else {}
    counters = flow.get("counters") if isinstance(flow.get("counters"), dict) else {}
    age = now - state.flow_sampled_at if state.flow_sampled_at is not None else None
    fresh = bool(state.connected and flow and age is not None and 0 <= age <= FLOW_FRESH_SECONDS)
    route = flow.get("route") if flow.get("route") in {"direct", "headroom_litellm"} else "unknown"
    active = flow.get("active") if isinstance(flow.get("active"), list) else []
    active = [entry for entry in active if isinstance(entry, dict)]
    observed_nodes = flow.get("nodes") if isinstance(flow.get("nodes"), dict) else {}
    current = active[0] if active else {}
    stage = FLOW_STAGE_LABELS.get(current.get("stage"), "active") if current.get("source") == "observed" else "active"
    pulse = FLOW_FRAMES[int(max(0, now) * 8) % len(FLOW_FRAMES)]
    nodes = []

    def node(name, label, evidence, active=False, kind="unknown"):
        nodes.append({"name": name, "label": label, "evidence": evidence, "active": bool(active and fresh), "pulse": pulse if active and fresh else "", "kind": kind})

    if not state.connected:
        label = "OFFLINE" if state.status.get("state") == "stopped" else "UNAVAILABLE"
        for name in ("Gateway", "Headroom", "LiteLLM", "llama.cpp"):
            node(name, label, "unknown", kind="offline")
    elif not flow:
        for name in ("Gateway", "Headroom", "LiteLLM", "llama.cpp"):
            node(name, "UNKNOWN", "unknown")
    elif not fresh:
        for name in ("Gateway", "Headroom", "LiteLLM", "llama.cpp"):
            node(name, "STALE", "unknown", kind="stale")
    else:
        gateway = observed_nodes.get("gateway") or {}
        gateway_observed = isinstance(gateway, dict) and gateway.get("source") == "observed"
        gateway_active = gateway_observed and gateway.get("state") == "active"
        gateway_idle = gateway_observed and gateway.get("state") == "idle"
        node("Gateway", stage if gateway_active else "idle" if gateway_idle else "UNKNOWN", "observed" if gateway_active or gateway_idle else "unknown", gateway_active, "gateway")
        only_benchmark = bool(active) and all(entry.get("kind") == "benchmark" for entry in active)
        for key, name in (("headroom", "Headroom"), ("litellm", "LiteLLM")):
            observed = observed_nodes.get(key) or {}
            if route == "direct" or only_benchmark:
                node(name, "BYPASSED", "configured route", kind="bypassed")
            elif route != "headroom_litellm" or not isinstance(observed, dict) or observed.get("source") != "inferred":
                node(name, "UNKNOWN", "unknown")
            elif observed.get("state") == "in_flight":
                node(name, "in route", "inferred", True, "proxy")
            elif observed.get("state") == "bypassed":
                node(name, "BYPASSED", "inferred", kind="bypassed")
            elif observed.get("state") == "idle":
                node(name, "idle route", "inferred", kind="proxy")
            else:
                node(name, "UNKNOWN", "unknown")
        backend = flow.get("backend") if isinstance(flow.get("backend"), dict) else {}
        measured = backend.get("source") == "authenticated_metrics" and backend.get("stale") is False and bool(backend.get("observed_at"))
        if measured and backend.get("state") in {"idle", "processing"}:
            processing = backend["state"] == "processing"
            node("llama.cpp", "processing " + flow_count(backend.get("processing")) if processing else "idle", "observed", processing, "backend")
        elif backend.get("stale") is True and backend.get("observed_at"):
            node("llama.cpp", "STALE metrics", "unknown", kind="stale")
        else:
            node("llama.cpp", "UNKNOWN", "unknown")
    return {"nodes": nodes, "counters": counters, "route": route, "fresh": fresh, "flow": flow, "current": current, "age": age}


def request_flow_lines(state: ViewState, now: float | None = None) -> list[str]:
    view = request_flow_view(state, now)
    flow, counters = view["flow"], view["counters"]
    backend = flow.get("backend") if isinstance(flow.get("backend"), dict) else {}
    lines = ["Route: " + safe_text(view["route"]) + " · sample " + ("fresh" if view["fresh"] else "offline/stale/unknown"),
             "Gateway generation " + safe_text(flow.get("generation") or "unknown", 32) + " · observed at " + safe_text(flow.get("observed_at") or "unknown", 40),
             "Gateway counters (this process generation, inference/benchmark posts only): " + " · ".join(key + " " + flow_count(counters.get(key)) for key in ("ingress", "accepted", "active", "completed", "failed", "rejected", "cancelled"))]
    for node in view["nodes"]:
        lines.append(node["name"] + ": " + node["label"] + " [" + node["evidence"] + "]")
    current = view["current"]
    if current:
        lines.append("Observed request: " + safe_text(current.get("kind", "unknown")) + " / " + safe_text(current.get("model", "unknown")) + " · elapsed " + metric(current.get("elapsed_ms"), " ms") + " · forwarded bytes " + flow_count(current.get("bytes_forwarded")))
        lines.append("First actual answer content: " + metric(current.get("first_content_ms"), " ms") + " (headers/reasoning/tool calls are not answer text)")
    last = flow.get("last") if isinstance(flow.get("last"), dict) else {}
    if last:
        lines.append("Last observed request: " + safe_text(last.get("model", "unknown")) + " / " + safe_text(last.get("outcome", "unknown")) + " · " + metric(last.get("duration_ms"), " ms") + " · error kind " + safe_text(last.get("error_kind") or "none"))
    lines += ["Backend cumulative token counters: prompt " + flow_count(backend.get("prompt_tokens_total")) + " / generated " + flow_count(backend.get("generated_tokens_total")) + " · epoch " + safe_text(backend.get("epoch") or "unknown", 32),
              "Completed counts gateway transport completion, not successful reasoning, tool execution, or task completion.",
              "Proxy phases are inferred from routing, not per-hop execution or compression measurements.",
              "DSH compaction: unobserved. This view does not measure or trigger compaction."]
    return lines


def render_request_flow(state: ViewState, width: int, height: int | None = None, now: float | None = None):
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    view = request_flow_view(state, now)
    counters, flow = view["counters"], view["flow"]
    tiny = width < 110 or (height is not None and height < 28)
    if tiny:
        short_names = ("GW", "HR", "LL", "llama")
        hops = []
        for short, node in zip(short_names, view["nodes"]):
            tag = "×" if node["kind"] == "bypassed" else "~" if node["evidence"] == "inferred" else "?" if node["evidence"] == "unknown" else ""
            hops.append(short + tag + node["pulse"])
        prefix = "OFFLINE " if not state.connected else "STALE " if flow and not view["fresh"] else ""
        route = "DIRECT " if view["route"] == "direct" else ""
        legend = "~inferred" if any(node["evidence"] == "inferred" for node in view["nodes"]) else "×bypass" if any(node["kind"] == "bypassed" for node in view["nodes"]) else "unknown" if any(node["evidence"] == "unknown" for node in view["nodes"]) else "observed"
        return Text("FLOW " + prefix + route + "→".join(hops) + " | " + legend + " | in" + flow_count(counters.get("ingress")) + " run" + flow_count(counters.get("active")) + " end" + flow_count(counters.get("completed")) + " fail" + flow_count(counters.get("failed")), style="dim", no_wrap=True, overflow="ellipsis")
    row = Table.grid(expand=True, padding=0)
    for index in range(7):
        row.add_column(ratio=1 if index % 2 == 0 else None, width=None if index % 2 == 0 else 3)
    cells = []
    for index, node in enumerate(view["nodes"]):
        label = "OBS " if node["evidence"] == "observed" else "INFER " if node["evidence"] == "inferred" else ""
        body = Text(label + (node["pulse"] + " " if node["pulse"] else "") + node["label"], no_wrap=True, overflow="ellipsis")
        color = "bright_cyan" if node["active"] and node["evidence"] == "observed" else "yellow" if node["evidence"] == "inferred" else "green" if node["evidence"] == "observed" else "dim"
        cells.append(Panel(body, title=Text(node["name"], style="bold"), border_style=color, padding=(0, 1)))
        if index < 3:
            cells.append(Text(" → ", style="cyan" if view["fresh"] else "dim"))
    row.add_row(*cells)
    counter_prefix = "Gateway OBS" if view["fresh"] else "Gateway LAST sample" if flow else "Gateway counters unavailable"
    counter_line = Text(counter_prefix + " · " + " · ".join(label + " " + flow_count(counters.get(key)) for label, key in (("in", "ingress"), ("accepted", "accepted"), ("active", "active"), ("done", "completed"), ("fail", "failed"), ("reject", "rejected"), ("cancel", "cancelled"))), style="dim", no_wrap=True, overflow="ellipsis")
    backend = flow.get("backend") if isinstance(flow.get("backend"), dict) else {}
    backend_label = "backend totals (stale)" if backend.get("stale") is True and backend.get("observed_at") else "backend totals" if backend.get("source") == "authenticated_metrics" and backend.get("stale") is False and backend.get("observed_at") else "backend totals (unknown)"
    proxies_bypassed = all(node["kind"] == "bypassed" for node in view["nodes"][1:3])
    route_note = "DIRECT: HR/LL bypassed" if view["route"] == "direct" else "Benchmark: HR/LL bypassed" if proxies_bypassed else "HR/LL = inferred route" if view["route"] == "headroom_litellm" else "Proxy route unknown"
    caption = ("STALE: animation frozen · " if not view["fresh"] and flow else "") + route_note + " · " + backend_label + " P " + flow_count(backend.get("prompt_tokens_total")) + " G " + flow_count(backend.get("generated_tokens_total")) + " · DSH compaction unobserved"
    return Group(row, counter_line, Text(caption, style="dim", no_wrap=True, overflow="ellipsis"))


def render_qualification_history():
    """Render observations, not executable profiles or current fit claims."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from .qualification_evidence import QUALIFICATIONS

    table = Table("Recorded scenario", "Allocation · actual in/out", "Timings and limits", expand=True)
    for index, record in enumerate(QUALIFICATIONS, 1):
        times = []
        if record.prefill_tps is not None and record.decode_tps is not None:
            times.append(f"Prefill {record.prefill_tps:,.2f} / decode {record.decode_tps:,.2f} tok/s")
        if record.first_text_s is not None:
            times.append(f"First text {record.first_text_s:,.2f} s")
        if record.wall_s is not None:
            times.append(f"Wall {record.wall_s:,.2f} s")
        if record.minimum_free_vram_mib is not None:
            times.append(f"Min {record.minimum_free_vram_mib:,.1f} MiB free VRAM")
        tokens = f"{record.allocation:,} ctx · {format_tokens(record.actual_input)} in / {format_tokens(record.actual_output)} out"
        table.add_row(Text(f"[{index}] {record.scenario}\n{record.route}"), Text(tokens), Text(" · ".join(times) + "\n" + record.outcome))
    sources = Text("Reports (repository-relative):\n" + "\n".join(f"[{i}] {r.report}" for i, r in enumerate(QUALIFICATIONS, 1)), style="dim", overflow="fold")
    note = Text("Historical observations only · — = not shown/reported · not live readiness or permission to run", style="yellow")
    return Panel(Group(note, table, sources), title="OBSERVATORY · QUALIFICATION HISTORY (READ-ONLY)", border_style="cyan")


def render_dashboard(state: ViewState, width: int = 110, height: int | None = None, now: float | None = None, *, resource_now=None):
    """Build one Rich renderable; pure enough for deterministic snapshot tests."""
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    compact = height is not None and height < (40 if width >= 110 else 44)
    brand = Text(" WATSON OBSERVATORY", style="bold bright_cyan")
    if not (height is not None and height < 28 and width < 110):
        brand.append("\nOne GPU · one active model · safe switching", style="dim")
    lifecycle_state = safe_text(state.status.get("state", "running" if state.connected else "stopped")).upper()
    badge = ("CONNECTED · " if state.connected else "OFFLINE · ") + lifecycle_state
    header = Table.grid(expand=True)
    header.add_column(ratio=1)
    header.add_column(justify="right")
    header.add_row(brand, Text(badge, style="bold green" if state.connected else "bold yellow"))

    resources = (state.status.get("resources") or {}) if state.connected else {}
    memory = Table.grid(expand=True, padding=(0, 2))
    memory.add_column()
    memory.add_column()
    memory.add_column()
    vram = resources.get("free_vram_mib")
    ram = resources.get("available_ram_gib")
    gpu = resources.get("gpu_utilization_percent")
    try:
        vram_text = f"{float(vram) / 1024:.2f} GiB free" if vram is not None else "unavailable"
    except (ValueError, TypeError):
        vram_text = "unavailable"
    try:
        ram_text = f"{float(ram):.2f} GiB free" if ram is not None else "unavailable"
    except (ValueError, TypeError):
        ram_text = "unavailable"
    resource_now = resource_now or datetime.now(timezone.utc)
    latest = state.resource_history.latest(resource_now)
    if not state.connected and latest is not None and latest.source != "system":
        latest = None
    if latest is None:
        memory.add_row(Text("VRAM  " + vram_text, style="cyan"), Text("RAM  " + ram_text, style="cyan"), Text(f"GPU  {safe_text(gpu) + '%' if gpu is not None else 'unavailable'}", style="cyan"))
    else:
        memory = Table.grid(expand=True, padding=(0, 1))
        for name in METRICS:
            memory.add_column(ratio=1)
        cells = []
        for name in METRICS:
            value = latest.percentages[name]
            label = format(value, ".0f") + "%" if value is not None else "—"
            cell = Text(f"{name.upper()} {label}", style=COLORS[name], no_wrap=True, overflow="ellipsis")
            size = 12 if width >= 110 else 6
            cell.append("  " + sparkline(state.resource_history.series(name, size, resource_now)[-size:]), style=COLORS[name])
            cells.append(cell)
        memory.add_row(*cells)

    catalog = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False, padding=(0, 1))
    catalog.add_column("", width=2)
    catalog.add_column("MODEL", ratio=3)
    catalog.add_column("CONTEXT", justify="right")
    catalog.add_column("STATE", justify="right")
    selected = state.selected
    selected_id = selected["id"] if selected else None
    active = state.status.get("active_model")
    display_models = state.visible_models
    window_start = 0
    chrome_rows = 26 if width >= 110 and compact else 22 if compact else 28
    page_size = max(2, (height - chrome_rows) // (1 if compact else 2)) if height is not None else len(display_models)
    if len(display_models) > page_size:
        index = next((i for i, row in enumerate(display_models) if row["id"] == selected_id), 0)
        window_start = min(max(0, index - page_size // 2), len(display_models) - page_size)
    for row in display_models[window_start:window_start + page_size]:
        is_selected = row["id"] == selected_id
        is_active = active in model_row_ids(row)
        name = Text(safe_text(row.get("name", row["id"])), style="bold bright_white" if is_selected else "white")
        if not compact:
            name.append("\n" + safe_text(row["id"]), style="dim")
        catalog.add_row(
            Text("❯" if is_selected else "", style="bright_cyan"), name,
            Text(format_tokens(row.get("context")), style="bright_cyan" if is_selected else "white"),
            Text("● LOADED" if is_active else "○ STANDBY", style="green" if is_active else "dim"),
            style="on #17333d" if is_selected else "",
        )
    if not display_models:
        catalog.add_row("", Text("No matching models", style="yellow"), "", "")

    details = Table.grid(padding=(0, 1), expand=True)
    details.add_column(style="dim", width=14)
    details.add_column(ratio=1)
    if selected:
        selected_family = {variant["id"] for variant in state.selected_variants}
        loaded_variant = active if active in selected_family else None
        loaded_context = state.status.get("context") if loaded_variant else None
        validation = safe_text(selected.get("validation", "unverified"))
        details.add_row("Profile context", Text(format_tokens(selected.get("context")) + " tokens · input + output"))
        details.add_row("Loaded context", Text(format_tokens(loaded_context) + " tokens" if loaded_context else "Not loaded", style="green" if loaded_context else "dim"))
        if len(state.selected_variants) > 1:
            details.add_row("Selected alias", Text(selected["id"]))
            details.add_row("Loaded alias", Text(loaded_variant or "Not loaded", style="green" if loaded_variant else "dim"))
        details.add_row("Evidence", Text(validation, style="yellow" if "unverified" in validation.lower() or "provisional" in validation.lower() else "cyan"))
        details.add_row("Capabilities", Text(" + ".join(safe_text(item) for item in selected.get("input", ["text"]))))
        details.add_row("Output budget", Text(format_tokens(selected.get("max_output_tokens")) + " tokens"))
        details.add_row("Presets", Text("  /  ".join(format_tokens(value) for value in context_presets(selected))))
        evidence = evidence_lines(selected, state.benchmark_result)
        details.add_row("Memory evidence", Text("\n".join(evidence[:2]), style="dim"))
        if not compact:
            details.add_row("Artifact", Text(safe_text(selected.get("path", "See registry"), 240), overflow="fold"))
    else:
        details.add_row("Add a GGUF", Text("Press A to register a model using an approved architecture profile."))

    activity = state.action or ("Active work — changes are refused, never scheduled" if state.status.get("busy") else "Choose model/context, then Enter to start or attach")
    scratch = state.scratch_advisory or {}
    scratch_timestamp = utc_timestamp(scratch.get("observed_at"))
    if (not state.action and not active and scratch.get("source") == "verified_scratch" and scratch_timestamp is not None
            and 0 <= ((resource_now or datetime.now(timezone.utc)) - scratch_timestamp).total_seconds() <= state.resource_history.fresh_seconds):
        activity = "VERIFIED SCRATCH · read-only · " + safe_text(scratch.get("alias", ""), 40) + " · " + format_tokens(scratch.get("context")) + " ctx · " + safe_text(scratch.get("phase", ""), 32)
    notice = Text(activity + "\n", style="bold yellow" if state.action or state.status.get("busy") else "bold green", no_wrap=compact, overflow="ellipsis")
    notice.append(safe_error(state.notice), style="white")
    if state.status.get("last_error"):
        notice.append("\nLast error: " + safe_error(state.status["last_error"]), style="yellow")
    if state.status.get("metadata_refresh_required"):
        notice.append("\nDSH catalog needs a metadata refresh after profile changes; see the DSH local-model registration guide.", style="yellow")
    if state.filter_text:
        notice.append("\nFilter: " + safe_text(state.filter_text) + "  (Esc clears)", style="cyan")
    keys = Text(" ↑↓ Select  C Context  Enter Start/attach/swap  S Stop STACK", style="bold cyan")
    if width >= 94:
        keys.append("\n B Bench Inkling  E Evidence  V History  H Charts  A Add  / Search  Q Detach  Z Zoom  ? Help", style="dim")
    else:
        keys.append("\n B Bench  V History  H Charts  A Add  Z Zoom  Q Detach  ? Help", style="dim")
    catalog_title = "MODEL LIBRARY · SELECTED CONTEXT" if any("_variants" in row for row in display_models) else "MODEL LIBRARY"
    if len(display_models) > page_size:
        catalog_title += f" · {window_start + 1}–{window_start + page_size}/{len(display_models)}"
    catalog_panel = Panel(catalog, border_style="cyan", title=Text(catalog_title, style="bold"))
    details_panel = Panel(details, border_style="dim", title="[dim]SELECTED PROFILE[/dim]")
    if compact and selected:
        grouped = len(state.selected_variants) > 1
        family = {variant["id"] for variant in state.selected_variants}
        loaded_alias = active if active in family else None
        loaded = format_tokens(state.status.get("context")) if loaded_alias else "—"
        memory_evidence = evidence_lines(selected, state.benchmark_result)
        if grouped:
            compact_lines = [f"Selected {selected['id']} · {format_tokens(selected.get('context'))} ctx · out {format_tokens(selected.get('max_output_tokens'))}", f"Loaded {loaded_alias or 'none'} · {loaded} ctx"]
            if width >= 110:
                compact_lines.append(safe_text(selected.get("validation", "unverified")))
        else:
            compact_lines = [f"Selected {format_tokens(selected.get('context'))} ctx · loaded {loaded} · output {format_tokens(selected.get('max_output_tokens'))}"]
            if width >= 110:
                compact_lines += [safe_text(selected.get("validation", "unverified")), "Capabilities: " + " + ".join(selected.get("input", ["text"]))]
        compact_lines += memory_evidence[:2] if width >= 110 else [" | ".join(memory_evidence[:2])]
        if width >= 110:
            compact_lines.append("E opens full evidence and benchmark results")
        summary = Text("\n".join(compact_lines), no_wrap=True, overflow="ellipsis")
        details_panel = Panel(summary, border_style="dim", title="[dim]SELECTED PROFILE[/dim]")
    if width >= 110:
        split = Table.grid(expand=True, padding=(0, 1))
        split.add_column(ratio=1, width=(width - 2) // 2)
        split.add_column(ratio=1, width=(width - 2) // 2)
        split.add_row(catalog_panel, details_panel)
        content = [split]
    else:
        content = [catalog_panel, details_panel]
    freshness = state.status.get("resources_stale")
    sample_title = "LIVE SYSTEM RESOURCES" if freshness is False else "STALE SYSTEM SAMPLE" if freshness is True else "SYSTEM SAMPLE · FRESHNESS UNAVAILABLE"
    if latest is not None and latest.source == "system":
        sample_title = "SYSTEM ONLY · LOCAL SAMPLER"
    elif not state.connected:
        sample_title = "SYSTEM ONLY · UNAVAILABLE"
    elif latest is None and state.resource_history.status_label(resource_now).startswith("stale"):
        sample_title = "STALE SYSTEM SAMPLE"
    if latest is not None:
        sample_title += f" · {(resource_now - latest.timestamp).total_seconds():.1f}s ago"
    compact_resources = height is not None and height < 24 and width < 110
    resource_panel = Group(Text(sample_title, style="dim", no_wrap=True, overflow="ellipsis"), memory) if compact_resources else Panel(
        memory, border_style="dim", title=Text(sample_title + " · NOT PROFILE USAGE", style="dim"))
    benchmark_summary = benchmark_lines(state.benchmark_result)
    benchmark_summary = benchmark_summary[-1:] if compact else benchmark_summary[:3] + (benchmark_summary[-1:] if len(benchmark_summary) > 3 else [])
    panels = [
        Panel(header, border_style="bright_cyan", padding=(0 if compact else 1, 1)),
        *([] if state.show_resource_charts and height is not None and height < 24 else [resource_panel]),
        render_request_flow(state, width, 24 if state.show_resource_charts else height, now),
        *([render_resource_history(state.resource_history, width, height, now=resource_now)] if state.show_resource_charts else content),
        *([Panel(Text("\n".join(benchmark_summary), no_wrap=compact, overflow="ellipsis"), title="[dim]BENCHMARK · MEASURED, NOT PREDICTED[/dim]", border_style="dim")] if not state.show_resource_charts and (not compact or width >= 110) else []),
        Panel(notice, border_style="yellow" if state.action or not state.connected else "green"),
        keys,
    ]
    return Group(*panels)


class KeyReader:
    """Nonblocking keys; Windows arrows and a POSIX raw-mode fallback."""

    def __enter__(self):
        self._saved = None
        if os.name != "nt" and sys.stdin.isatty():
            import termios
            import tty
            self._saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *args):
        if self._saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
            self._saved = None

    @contextmanager
    def cooked(self):
        saved = self._saved
        if saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
        try:
            yield
        finally:
            if saved is not None:
                import tty
                tty.setcbreak(sys.stdin.fileno())

    def read(self) -> str | None:
        if os.name == "nt":
            import msvcrt
            if not msvcrt.kbhit():
                return None
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch())
        else:
            import select
            if not select.select([sys.stdin], [], [], 0)[0]:
                return None
            key = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore")
            if key == "\x1b" and select.select([sys.stdin], [], [], 0.02)[0]:
                suffix = os.read(sys.stdin.fileno(), 2).decode("ascii", errors="ignore")
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(suffix, "escape")
        return {"\r": "enter", "\n": "enter", "\x1b": "escape", "\x03": "quit"}.get(key, key.lower())


def _read_choice(console, prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = console.input(prompt + suffix + ": ").strip()
    return value or (default if default is not None else "")


def choose_context(console, model: Mapping[str, Any]) -> int | None:
    from rich.panel import Panel
    from rich.text import Text
    presets = context_presets(model)
    lines = [f"{index}. {value:,} tokens" for index, value in enumerate(presets, 1)]
    lines.extend(["C. Custom token count", "Enter. Cancel", "", "Allocation includes input + output. Larger allocation does not prove model quality."])
    console.print(Panel(Text("\n".join(lines)), title=Text("Context · " + safe_text(model.get("name", model["id"]))), border_style="cyan"))
    answer = _read_choice(console, "Choose preset or C")
    if not answer:
        return None
    if answer.lower() == "c":
        answer = _read_choice(console, "Context tokens (multiple of 256)")
        if not answer:
            return None
        return validate_context(answer, model)
    try:
        index = int(answer) - 1
        if 0 <= index < len(presets):
            return validate_context(presets[index], model)
    except ValueError:
        pass
    raise ValueError("Choose one of the displayed preset numbers or C.")


def profile_context_options(variants: Iterable[Mapping[str, Any]]) -> list[tuple[str, int]]:
    options = []
    for variant in variants:
        for context in context_presets(variant):
            try:
                context = validate_context(context, variant)
            except ValueError:
                continue
            options.append((str(variant["id"]), context))
    return sorted(set(options), key=lambda item: (item[1], item[0]))


def choose_context_profile(console, variants: list[dict[str, Any]], selected_id: str | None = None) -> tuple[str, int] | None:
    """Choose an existing runtime profile and context without combining ranges."""
    from rich.panel import Panel
    from rich.text import Text
    options = profile_context_options(variants)
    preferred = next((row for row in variants if default_inkling_profile(row)), None)
    lines = []
    for index, (alias, context) in enumerate(options, 1):
        suffix = " · preferred selection" if preferred and alias == preferred["id"] else (
            " · 100K fallback" if alias == "inkling2-mini-100k" else " · opt-in" if alias == INKLING_DEFAULT_ALIAS else "")
        lines.append(f"{index}. {context:,} tokens · {alias}{suffix}")
    lines += ["C. Custom within one approved profile", "Enter. Cancel", "", "Selecting a profile never loads it.", "Each choice retains its exact engine, alias, cache and resource requirements."]
    console.print(Panel(Text("\n".join(lines)), title="Inkling context profiles", border_style="cyan"))
    answer = _read_choice(console, "Choose context profile or C")
    if not answer:
        return None
    if answer.lower() != "c":
        try:
            index = int(answer) - 1
            if 0 <= index < len(options):
                return options[index]
        except ValueError:
            pass
        raise ValueError("Choose one displayed context profile or C.")
    for index, variant in enumerate(variants, 1):
        minimum = int(variant.get("minimum_context", 512))
        maximum = context_limit(variant)
        console.print(Text(f"{index}. {variant['id']} · approved {minimum:,}–{maximum:,} tokens"))
    default = str(next((i for i, row in enumerate(variants, 1) if row["id"] == selected_id), 1))
    answer = _read_choice(console, "Approved profile for custom allocation", default)
    try:
        index = int(answer) - 1
        if not 0 <= index < len(variants):
            raise ValueError
        variant = variants[index]
    except ValueError:
        raise ValueError("Choose an approved profile number.") from None
    value = _read_choice(console, "Context tokens (multiple of 256)")
    return (variant["id"], validate_context(value, variant)) if value else None


def registration_wizard(console, templates: list[dict[str, Any]], existing_ids: Iterable[str]) -> dict[str, Any] | None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from .registration_workflow import RegistrationDraft, registration_options
    options = registration_options(templates, context_limit)
    if not options:
        raise ValueError("No approved profile fits the provisional context limit. Ask the operator to add a reviewed profile first.")
    console.print(Panel(Text("Register a model, not a new engine.\nChoose an existing architecture profile only if it matches the new model.\nNo executable paths or arbitrary flags are accepted here.\nNew artifacts start UNVERIFIED at no more than 8,192 context tokens.\nPress Enter at the first question to cancel."), title="ADD GGUF", border_style="yellow"))
    for index, option in enumerate(options, 1):
        template = option.template
        console.print(Text(f"{index}. {safe_text(template.get('name', template['id']))}  [{safe_text(template.get('architecture', template['id']))}] · {option.minimum_context:,}–{option.maximum_context:,} tokens"))
    choice = _read_choice(console, "Matching architecture profile")
    if not choice:
        return None
    try:
        index = int(choice) - 1
        if not 0 <= index < len(options):
            raise ValueError
        option = options[index]
        template = option.template
    except ValueError:
        raise ValueError("Choose an existing profile number.") from None
    confirmed = _read_choice(console, "Type MATCH to confirm the same model architecture (not merely GGUF format)")
    if confirmed != "MATCH":
        return None
    files = discover_ggufs()
    if files:
        table = Table("#", "Discovered GGUF", "Size", show_lines=False)
        for index, path in enumerate(files, 1):
            try:
                size = f"{path.stat().st_size / (1024 ** 3):.2f} GiB"
            except OSError:
                size = "unavailable"
            table.add_row(str(index), Text(str(path)), size)
        console.print(table)
    artifact = _read_choice(console, "Model file number or full GGUF path")
    if not artifact:
        return None
    if artifact.isdigit() and 1 <= int(artifact) <= len(files):
        artifact = str(files[int(artifact) - 1])
    validated = validate_gguf(artifact)
    default_id = re.sub(r"[^a-z0-9._-]+", "-", validated.stem.lower()).strip("-._")[:64] or "new-model"
    model_id = _read_choice(console, "Unique model id", default_id)
    name = _read_choice(console, "Display name", validated.stem[:100])
    context = _read_choice(console, f"Initial context ({option.minimum_context:,}–{option.maximum_context:,} tokens)", str(option.maximum_context))
    mmproj = None
    if "image" in (template.get("input") or []):
        console.print(Text("This vision profile requires the projector matching the NEW artifact; the old projector is not assumed compatible.", style="yellow"))
        mmproj = _read_choice(console, "Matching projector GGUF path")
    model = build_registration(template, model_id=model_id, name=name, path=validated, context=context, existing_ids=existing_ids, mmproj=mmproj)
    draft = RegistrationDraft.capture(option, model)
    review = "\n".join(f"{label}: {safe_text(value, 1024)}" for label, value in draft.review_fields())
    console.print(Panel(Text(review + "\n\nRegistration saves this profile only. It does not load or qualify the model."), title="REVIEW GGUF PROFILE", border_style="cyan"))
    return draft.confirmed_payload(_read_choice(console, "Type REGISTER to save"))


class _ClientOnlyLifecycle:
    """Read-only fallback for callers injecting an existing client, never a launcher."""
    def __init__(self, client):
        self.client = client

    def status(self):
        status = self.client.status()
        return {**status, "running": True, "state": "running", "owned": False}

    def start(self, **kwargs):
        raise ValueError("A lifecycle controller is required to start or attach the stack.")

    def stop(self):
        raise ValueError("A lifecycle controller is required to stop the stack.")


def _observe_scratch():
    try:
        from .scratch_observation import observe_scratch
    except ImportError:
        return None
    return observe_scratch()


@dataclass(frozen=True)
class TerminalPoll:
    status: dict[str, Any]
    models: list[dict[str, Any]]


@dataclass(frozen=True)
class PreviewEntry:
    profile: dict[str, Any]
    context: int | None
    expires_at: float
    preview: dict[str, Any] | None


class TerminalController:
    """UI choices and rendering state, not process ownership or launch sequencing."""
    def __init__(self, client, lifecycle, registry_path=DEFAULT_REGISTRY, direct=False, *, resource_sampler=None, scratch_observer=None):
        from .lifecycle import StackLifecycle
        if isinstance(lifecycle, StackLifecycle):
            from .runtime import resource_snapshot
            resource_sampler = resource_sampler or resource_snapshot
            scratch_observer = scratch_observer or _observe_scratch
        self.client = client
        self.lifecycle = lifecycle
        self.registry_path = registry_path
        self.direct = direct
        self.resource_sampler = resource_sampler
        self.scratch_observer = scratch_observer
        self.context_choices: dict[str, int] = {}
        self._previews: dict[str, PreviewEntry] = {}
        self.state = ViewState(normalize_models([], read_registry(registry_path)), {"state": "stopped", "running": False})
        display_rows = grouped_model_rows(self.state.models)
        preferred = next((row for row in self.state.models if default_inkling_profile(row)), None)
        self.state.selected_id = preferred["id"] if preferred else (display_rows[0]["id"] if display_rows else None)
        self.state.notice = "Choose a model and context. Enter starts or attaches; S stops the whole stack."

    def fetch(self):
        try:
            status = self.lifecycle.status()
        except (OSError, StackError) as exc:
            if self.resource_sampler is None:
                raise
            status = {"state": "unknown", "running": False, "message": "Status unavailable: " + safe_error(exc)}
        if not isinstance(status, dict):
            raise ValueError("Lifecycle status was not an object.")
        status = dict(status)
        status.pop("_system_sample", None)
        status.pop("_scratch_advisory", None)
        # Only the authenticated lifecycle can establish a resident or flow.
        if status.get("running"):
            catalog = status["models"] if isinstance(status.get("models"), list) else self.client.models()
        else:
            catalog = []
            if self.resource_sampler is not None:
                try:
                    values = self.resource_sampler()
                    if isinstance(values, dict):
                        status["_system_sample"] = (datetime.now(timezone.utc), values)
                except (OSError, StackError, ValueError):
                    pass
        if not (status.get("running") and status.get("active_model")) and self.scratch_observer is not None:
            try:
                advisory = self.scratch_observer()
                if isinstance(advisory, dict) and advisory.get("source") == "verified_scratch":
                    status["_scratch_advisory"] = advisory
            except (OSError, StackError, ValueError):
                pass
        rows = normalize_models(catalog, read_registry(self.registry_path), status)
        # Read-only historical evidence: no benchmark, subprocess or file write.
        try:
            from .benchmark import benchmark_preview
            root = Path(self.registry_path).resolve().parent.parent
            state_dir = getattr(self.lifecycle, "state_dir", root / ".runtime" / "local-stack")
            now = time.monotonic()
            previews = {}
            for row in rows:
                model_id = row["id"]
                row.pop("benchmark_preview", None)
                context = self.context_choices.get(model_id, row.get("context"))
                entry = self._previews.get(model_id)
                if entry is None or entry.expires_at <= now or entry.context != context or entry.profile != row:
                    profile = copy.deepcopy(row)
                    preview = None
                    try:
                        for field in ("path", "engine", "mmproj"):
                            if profile.get(field) and not Path(profile[field]).is_absolute():
                                profile[field] = str((root / profile[field]).resolve())
                        preview = benchmark_preview(profile, context, state_dir=state_dir)
                    except (ValueError, KeyError, TypeError, OSError, RuntimeError):
                        pass
                    entry = PreviewEntry(dict(row), context, now + PREVIEW_TTL_SECONDS, preview)
                if entry.preview is not None:
                    row["benchmark_preview"] = entry.preview
                previews[model_id] = entry
            self._previews = previews
        except ImportError:
            pass
        status["models"] = rows
        return TerminalPoll(status, rows)

    def apply_snapshot(self, snapshot):
        if isinstance(snapshot, TerminalPoll):
            status, rows = dict(snapshot.status), [dict(row) for row in snapshot.models]
        else:
            catalog, status = snapshot
            status = dict(status)
            rows = normalize_models(catalog, self.state.models, status)
        system_sample = status.pop("_system_sample", None)
        scratch_advisory = status.pop("_scratch_advisory", None)
        nested = status.get("gateway_status") or {}
        if nested:
            status = {**nested, **status}
        previous_state = self.state.status.get("state")
        self.state.connected = bool(status.get("running"))
        if not self.state.connected:
            for key in ("active_model", "context", "resources", "request_flow", "gateway_status"):
                status.pop(key, None)
        status.pop("_system_sample", None)
        status.pop("_scratch_advisory", None)
        self.state.scratch_advisory = scratch_advisory
        self.state.status = status
        self.state.resource_history.observe(status, connected=self.state.connected, now=datetime.now(timezone.utc), system_sample=system_sample)
        self.state.flow_sampled_at = time.monotonic() if isinstance(status.get("request_flow"), dict) else None
        if status.get("state") == "unknown" and status.get("message"):
            self.state.notice = safe_text(status["message"]) + " No unverified process will be adopted or stopped."
        elif (self.state.connected and previous_state == "unknown" and not self.state.action
                and (self.state.notice.startswith("Status unavailable:") or self.state.notice.endswith("No unverified process will be adopted or stopped."))):
            self.state.notice = "Verified Watson gateway connected. Observing its current state; selection unchanged."
        for row in rows:
            if row["id"] in self.context_choices:
                choice = self.context_choices[row["id"]]
                if choice != row.get("context"):
                    row["validation"] = "Selected context; measurement evidence does not automatically transfer"
                row["context"] = choice
        self.state.models = rows

    def poll_failed(self, error):
        self.state.connected = False
        self.state.status = {"state": "unknown", "running": False, "busy": False}
        self.state.scratch_advisory = None
        self.state.resource_history.observe({}, connected=False)
        self.state.flow_sampled_at = None
        self.state.notice = "Status unavailable: " + safe_error(error) + ". No start, stop or retry was scheduled."

    def choose_context(self, context):
        model = self.state.selected
        if not model:
            raise ValueError("Select a registered model first.")
        context = validate_context(context, model)
        self.context_choices[model["id"]] = context
        previous = model.get("context")
        model["context"] = context
        if previous != context:
            model["validation"] = "Selected context; apply with Enter (not loaded or newly measured)"
        self.state.notice = f"Selected {context:,} context tokens. Press Enter to apply with a safe start/attach/swap."

    def choose_profile_context(self, model_id, context):
        variants = self.state.selected_variants
        if not any(variant["id"] == model_id for variant in variants):
            raise ValueError("Choose a context from this model's approved profiles.")
        model = next(variant for variant in variants if variant["id"] == model_id)
        context = validate_context(context, model)
        self.state.selected_id = model_id
        if len(variants) > 1:
            self.state.inkling_variant_id = model_id
        self.choose_context(context)

    def start_selected(self, selection=None):
        model = selection or self.state.selected
        if not model:
            raise ValueError("Select a registered model first.")
        return self.lifecycle.start(model=model["id"], context=model["context"], direct=self.direct)

    def benchmark_selected(self, profile, selection=None):
        model = selection or self.state.selected
        if not model or model["id"] not in INKLING_BENCHMARK_MODELS:
            raise ValueError("Benchmark optimization is Inkling-only; Saluki remains unchanged.")
        if not self.state.connected:
            raise ValueError("Start or attach the stack before benchmarking.")
        if profile == "near-context" and model["context"] > 8192:
            raise ValueError("Near-context benchmark is available only at configured context <= 8,192 tokens.")
        if profile == "vision-quick" and not supports_vision_quick(model):
            raise ValueError("Native vision quick check requires an Inkling profile with image input and a registered projector.")
        if profile not in {"quick", "near-context", "vision-quick"}:
            raise ValueError("Only known bounded Inkling benchmarks are available.")
        return self.client.benchmark(model["id"], context=model["context"], profile=profile)

    def apply_result(self, result):
        if not isinstance(result, dict):
            self.state.notice = "Operation completed; refreshing verified lifecycle state."
            return
        if result.get("source") == "measured" and isinstance(result.get("performance"), dict):
            self.state.benchmark_result = result
        if result.get("status") in {"uncertain", "refused"}:
            self.state.notice = safe_text(result.get("message", "Operation not confirmed; no success or deferred retry is assumed."))
        else:
            self.state.notice = safe_text(result.get("message", "Operation completed; refreshing verified lifecycle state."))
        loaded = result.get("model")
        if result.get("metadata_refresh_required") or (isinstance(loaded, dict) and loaded.get("metadata_refresh_required")):
            self.state.notice += " DSH model metadata needs refreshing if its saved context differs."


def _background(call: Callable, *args) -> Future:
    """Finite daemon request: exiting the UI must not wait for a model load."""
    future: Future = Future()

    def work():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(call(*args))
        except BaseException as exc:
            future.set_exception(exc)

    Thread(target=work, name="model-selector-request", daemon=True).start()
    return future


def run(
    base_url: str = DEFAULT_URL,
    registry_path: str | Path = DEFAULT_REGISTRY,
    *,
    client=None,
    lifecycle=None,
    console=None,
    refresh_seconds: float = STATUS_POLL_SECONDS,
    snapshot: bool = False,
    direct: bool = False,
) -> int:
    """Persistent terminal; all workload lifetime decisions belong to lifecycle."""
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        print("The terminal UI needs Rich. Run the approved local-stack dependency installer first.", file=sys.stderr)
        return 2
    injected_client = client is not None
    if client is None:
        from .client import StackClient
        client = StackClient(base_url)
    if lifecycle is None:
        if injected_client:
            lifecycle = _ClientOnlyLifecycle(client)
        else:
            from .lifecycle import StackLifecycle
            lifecycle = StackLifecycle(client=client, registry_path=registry_path, base_url=base_url)
    if console is None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
        console = Console()
    controller = TerminalController(client, lifecycle, registry_path, direct)
    state = controller.state
    if snapshot or not sys.stdin.isatty() or not console.is_terminal:
        try:
            controller.apply_snapshot(controller.fetch())
        except Exception as exc:
            controller.poll_failed(exc)
            state.notice += f" Open {START_COMMAND} for interactive controls."
        console.print(render_dashboard(state, console.width, console.height))
        if state.status.get("state") == "unknown":
            console.print(Text("Open the persistent controller: " + START_COMMAND, style="yellow"))
        if not snapshot:
            console.print(Text("Interactive keyboard controls require a real terminal. No state was changed.", style="yellow"))
        return 0 if state.status.get("state") != "unknown" else 1

    # Even the very first status call runs outside the drawing/input loop.
    pending_poll: Future | None = _background(controller.fetch)
    pending_action: Future | None = None
    next_poll = time.monotonic() + refresh_seconds
    next_frame = time.monotonic() + 1.0
    last_size = tuple(console.size)
    next_resize_check = 0.0

    def submit(label: str, call: Callable, *args):
        nonlocal pending_action
        if pending_action is not None or state.status.get("busy"):
            state.notice = "Busy: operation refused. Nothing was queued or scheduled; retry explicitly when idle."
            return
        state.action = label
        state.notice = "Checking lifecycle ownership, admission, resources and readiness."
        pending_action = _background(call, *args)

    try:
        with KeyReader() as keys, Live(render_dashboard(state, *last_size), console=console, screen=True, auto_refresh=False) as live:
            while True:
                key = keys.read()
                if key in ("q", "quit"):
                    break
                now = time.monotonic()
                if pending_poll is not None and pending_poll.done():
                    try:
                        controller.apply_snapshot(pending_poll.result())
                    except Exception as exc:
                        controller.poll_failed(exc)
                    pending_poll = None
                    next_frame = 0
                if pending_action is not None and pending_action.done():
                    try:
                        controller.apply_result(pending_action.result())
                    except Exception as exc:
                        state.notice = "Refused/failed: " + safe_error(exc) + ". Nothing is scheduled for later."
                    pending_action, state.action = None, None
                    next_poll = 0
                    next_frame = 0
                if now >= next_poll and pending_poll is None:
                    pending_poll = _background(controller.fetch)
                    next_poll = now + max(0.5, refresh_seconds)
                if key is not None:
                    next_frame = 0
                if key in ("up", "k"):
                    state.move(-1)
                elif key in ("down", "j"):
                    state.move(1)
                elif key == "escape":
                    state.filter_text = ""
                elif key == "r":
                    next_poll = 0
                elif key == "h":
                    state.show_resource_charts = not state.show_resource_charts
                elif key == "z":
                    span = state.resource_history.zoom()
                    state.notice = f"System history: last {span // 60} minutes. Sampling cadence unchanged."
                elif key in ("enter", "l") and state.selected:
                    submit("Starting/attaching " + safe_text(state.selected["name"]) + "…", controller.start_selected, copy.deepcopy(state.selected))
                elif key in ("c", "a", "s", "/", "b", "e", "v", "?"):
                    if (pending_action is not None or state.status.get("busy")) and key not in ("/", "e", "v", "?"):
                        state.notice = "Busy: action refused, never queued. Retry explicitly once idle."
                        continue
                    live.stop()
                    try:
                        with keys.cooked():
                            if key == "?":
                                console.print(Panel(Text(
                                    "VIEW ONLY\n↑ ↓ or J K   Select a model\n/   Filter models; Esc clears\n"
                                    "H   Toggle full charts; compact resources stay on the dashboard\n"
                                    "Z   Cycle the visible history: 2, 5, 15 minutes\n"
                                    "E   Inspect benchmark and request-stage evidence\nV   Historical qualification (read-only)\nR   Refresh status\n"
                                    "Q   Detach; does not stop Watson\n\n"
                                    "CONTROLS (REFUSED WHILE BUSY)\nC   Choose a context/profile before starting\n"
                                    "Enter or L   Start, attach, or swap to the selection\n"
                                    "S   Confirm stop of the entire owned stack\n"
                                    "A   Review and register a GGUF without loading it\n"
                                    "B   Confirm a bounded Inkling benchmark"), title="WATSON · KEYS", border_style="cyan"))
                                _read_choice(console, "Press Enter to return")
                            elif key == "c" and state.selected:
                                if len(state.selected_variants) > 1:
                                    choice = choose_context_profile(console, state.selected_variants, state.selected["id"])
                                    if choice is not None:
                                        controller.choose_profile_context(*choice)
                                else:
                                    choice = choose_context(console, state.selected)
                                    if choice is not None:
                                        controller.choose_context(choice)
                            elif key == "a":
                                if not state.connected:
                                    raise ValueError("Start/attach the stack before registering a new GGUF.")
                                model = registration_wizard(console, state.models, [row["id"] for row in state.models])
                                if model:
                                    submit("Registering model profile…", client.register, model)
                            elif key == "s":
                                answer = _read_choice(console, "Type STOP to stop the ENTIRE owned stack; busy work will refuse it")
                                if answer == "STOP":
                                    submit("Stopping and verifying the owned stack…", lifecycle.stop)
                            elif key == "/":
                                state.filter_text = _read_choice(console, "Filter models (blank clears)")
                            elif key == "e":
                                selected = state.selected or {}
                                console.print(Panel(Text("\n".join(evidence_lines(selected, state.benchmark_result) + [""] + benchmark_lines(state.benchmark_result) + [""] + request_flow_lines(state))), title="Evidence · estimates are not measurements", border_style="cyan"))
                                _read_choice(console, "Press Enter to return")
                            elif key == "v":
                                console.print(render_qualification_history())
                                _read_choice(console, "Press Enter to return")
                            elif key == "b":
                                if not state.selected or state.selected["id"] not in INKLING_BENCHMARK_MODELS:
                                    raise ValueError("Benchmark optimization is Inkling-only, not Saluki.")
                                if not state.connected:
                                    raise ValueError("Start/attach before benchmarking.")
                                options = {"1": "quick", "2": "near-context"}
                                menu = ["Exclusive Inkling benchmark. AC power and resource gates apply.",
                                        "1. Quick synthetic prefill/decode + natural-EOS quality check",
                                        "2. Near-context synthetic pressure (configured context must be <= 8,192 tokens)"]
                                if supports_vision_quick(state.selected):
                                    options["3"] = "vision-quick"
                                    menu.append("3. Native vision quick check: generated color/position, OCR and structured tasks")
                                if guard := experimental_guard_line(state.selected):
                                    menu.append(guard)
                                menu.append("Enter cancels. No arbitrary prompts or commands.")
                                console.print(Text("\n".join(menu), style="yellow"))
                                profile_choice = _read_choice(console, "Choose benchmark")
                                if profile_choice in options:
                                    profile = options[profile_choice]
                                    if _read_choice(console, "Type BENCH to reserve the model for this bounded run") == "BENCH":
                                        submit("Exclusive Inkling benchmark in progress…", controller.benchmark_selected, profile, copy.deepcopy(state.selected))
                    except (ValueError, OSError) as exc:
                        state.notice = safe_error(exc)
                    finally:
                        live.start()
                now = time.monotonic()
                if now >= next_resize_check:
                    size = tuple(console.size)
                    if size != last_size:
                        last_size, next_frame = size, 0
                    next_resize_check = now + 0.10
                if now >= next_frame:
                    live.update(render_dashboard(state, *last_size, now), refresh=True)
                    flow_animating = any(node["active"] for node in request_flow_view(state, now)["nodes"])
                    next_frame = now + (0.125 if flow_animating else 1.0)
                if key is None:
                    time.sleep(KEY_POLL_SECONDS)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if pending_poll is not None:
            pending_poll.cancel()
    console.print(Text("Selector detached. Stack and any admitted request were left running.", style="cyan"))
    return 0
