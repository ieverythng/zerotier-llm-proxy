"""Validated data-only model profiles; no shell or arbitrary executable registration."""
from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path


class StackError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ALLOWED_EXTRA_FLAGS = {"--no-op-offload", "--no-mmap", "--no-context-shift"}
ALLOWED_KEYS = {
    "id", "name", "path", "engine", "context", "context_presets", "max_output_tokens", "default_output_tokens",
    "input", "kv_cache", "batch_size", "ubatch_size", "gpu_layers", "mmproj", "mmproj_gpu",
    "extra_args", "reasoning_effort", "validation", "tested_prompt_tokens",
    "estimated_vram_mib", "minimum_context", "maximum_context", "profile", "notes",
    "reasoning", "load_mode", "image_min_tokens", "flash_attention", "startup_timeout_seconds", "fit_target_mib",
    "context_verified", "reasoning_budget", "temperature", "top_p", "top_k", "min_p", "experimental_resources",
}
EXPERIMENTAL_RESOURCE_KEYS = {"minimum_free_vram_mib", "minimum_available_ram_gib"}


def _integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise StackError(f"{name} must be an integer between {lower} and {upper}.")
    return value


def _validate_experimental_resources(policy):
    if not isinstance(policy, dict) or set(policy) != EXPERIMENTAL_RESOURCE_KEYS:
        raise StackError("experimental_resources requires exactly minimum_free_vram_mib and minimum_available_ram_gib.")
    _integer(policy["minimum_free_vram_mib"], "experimental_resources.minimum_free_vram_mib", 512, 2048)
    ram = policy["minimum_available_ram_gib"]
    if type(ram) not in (float, int) or not 1.25 <= ram <= 2:
        raise StackError("experimental_resources.minimum_available_ram_gib must be a number between 1.25 and 2.")


def resource_limits(resources: dict, model: dict | None = None, *, startup: bool = False) -> dict:
    """Resolve detached admission limits for a validated, trusted selected profile."""
    policy = model.get("experimental_resources") if model is not None else None
    if policy is not None:
        _validate_experimental_resources(policy)
    steady = policy if policy is not None else resources
    return {
        "minimum_free_vram_mib": steady.get("minimum_free_vram_mib", 2048),
        "minimum_available_ram_gib": (resources.get("startup_minimum_available_ram_gib", 3) if startup else steady.get("minimum_available_ram_gib", 2)),
        "experimental": policy is not None,
    }


def validate_model(model: dict, *, trusted_engines: set[str] | None = None, trusted_registry: bool = False) -> dict:
    if not isinstance(model, dict):
        raise StackError("A model must be an object.")
    unknown = set(model) - ALLOWED_KEYS
    if unknown:
        raise StackError("Unsupported model fields: " + ", ".join(sorted(unknown)))
    result = deepcopy(model)
    if "experimental_resources" in result:
        if not trusted_registry:
            raise StackError("experimental_resources may only be set in the trusted model registry, not a runtime registration.", 403)
        _validate_experimental_resources(result["experimental_resources"])
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", str(result.get("id", ""))):
        raise StackError("Model id must be 1–96 letters, digits, dots, underscores or hyphens.")
    if not isinstance(result.get("name"), str) or not result["name"].strip() or len(result["name"]) > 160:
        raise StackError("A model needs a nonempty display name of at most 160 characters.")
    for field in ("path", "engine"):
        if not isinstance(result.get(field), str) or not Path(result[field]).is_absolute():
            raise StackError(f"{field} must be an absolute path.")
    if Path(result["path"]).suffix.lower() != ".gguf":
        raise StackError("Model path must identify a GGUF file.")
    if Path(result["engine"]).name.lower() not in {"llama-server.exe", "llama-server"}:
        raise StackError("Only a configured llama-server executable may be used.")
    if trusted_engines is not None and os.path.normcase(str(Path(result["engine"]).resolve())) not in trusted_engines:
        raise StackError("New executables cannot be trusted through HTTP; use an existing engine profile.", 403)
    result.setdefault("minimum_context", 512)
    result.setdefault("maximum_context", 262144)
    _integer(result["minimum_context"], "minimum_context", 512, 1048576)
    _integer(result["maximum_context"], "maximum_context", result["minimum_context"], 1048576)
    _integer(result.get("context"), "context", result["minimum_context"], result["maximum_context"])
    result["context"] = normalize_context(result["context"])
    if result["context"] > result["maximum_context"]:
        raise StackError("Aligned context exceeds maximum_context.")
    result.setdefault("max_output_tokens", 2048)
    _integer(result["max_output_tokens"], "max_output_tokens", 1, result["context"] - 1)
    if "default_output_tokens" in result:
        _integer(result["default_output_tokens"], "default_output_tokens", 1, result["max_output_tokens"])
    result.setdefault("context_presets", [result["context"]])
    if not isinstance(result["context_presets"], list):
        raise StackError("context_presets must be a list.")
    result["context_presets"] = sorted(set(normalize_context(_integer(v, "context preset", result["minimum_context"], result["maximum_context"])) for v in result["context_presets"]))
    if any(v > result["maximum_context"] for v in result["context_presets"]):
        raise StackError("Aligned context preset exceeds maximum_context.")
    for field, default, lower, upper in (("batch_size", 512, 1, 4096), ("ubatch_size", 128, 1, 2048), ("fit_target_mib", 2304, 512, 32768), ("estimated_vram_mib", 0, 0, 131072), ("image_min_tokens", 1024, 0, 16384), ("startup_timeout_seconds", 180, 10, 900)):
        result.setdefault(field, default)
        _integer(result[field], field, lower, upper)
    result.setdefault("gpu_layers", 99)
    if result["gpu_layers"] != "auto":
        _integer(result["gpu_layers"], "gpu_layers", 0, 999)
    if result["ubatch_size"] > result["batch_size"]:
        raise StackError("ubatch_size cannot exceed batch_size.")
    result.setdefault("kv_cache", "q8_0")
    if result["kv_cache"] not in {"f16", "q8_0", "q4_0"}:
        raise StackError("kv_cache must be f16, q8_0 or q4_0.")
    result.setdefault("input", ["text"])
    if not isinstance(result["input"], list) or not result["input"] or any(v not in {"text", "image"} for v in result["input"]):
        raise StackError("input may contain text and image only.")
    if result.get("mmproj"):
        if not isinstance(result["mmproj"], str) or not Path(result["mmproj"]).is_absolute() or Path(result["mmproj"]).suffix.lower() != ".gguf":
            raise StackError("mmproj must be an absolute GGUF path.")
    if "mmproj_gpu" in result:
        if not isinstance(result["mmproj_gpu"], bool):
            raise StackError("mmproj_gpu must be boolean.")
        if not result.get("mmproj"):
            raise StackError("mmproj_gpu requires an explicit projector.")
    if "image" in result["input"] and not result.get("mmproj"):
        raise StackError("Image-capable profiles require an explicit projector.")
    result.setdefault("extra_args", [])
    if not isinstance(result["extra_args"], list) or any(v not in ALLOWED_EXTRA_FLAGS for v in result["extra_args"]):
        raise StackError("extra_args supports only " + ", ".join(sorted(ALLOWED_EXTRA_FLAGS)) + "; launch paths, binding and credentials are fixed.")
    if result.get("reasoning_effort") not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise StackError("Unsupported reasoning_effort.")
    if result.get("reasoning", "off") not in {"off", "on", "auto"}:
        raise StackError("Unsupported reasoning mode.")
    if result.get("load_mode", "dio") not in {"dio", "mmap"}:
        raise StackError("Unsupported load_mode.")
    if result.get("flash_attention", "on") not in {"on", "off", "auto"}:
        raise StackError("Unsupported flash_attention.")
    for field, lower, upper in (("temperature", 0, 2), ("top_p", 0, 1), ("min_p", 0, 1)):
        if field in result and (isinstance(result[field], bool) or not isinstance(result[field], (int, float)) or not lower <= result[field] <= upper):
            raise StackError(f"{field} is outside the supported sampler range.")
    for field, lower, upper in (("top_k", 0, 10000), ("reasoning_budget", 0, 32768)):
        if field in result:
            _integer(result[field], field, lower, upper)
    if not isinstance(result.get("context_verified", False), bool):
        raise StackError("context_verified must be boolean.")
    result.setdefault("validation", "historically verified" if result.get("context_verified") else "provisional")
    if not isinstance(result["validation"], str) or len(result["validation"]) > 160:
        raise StackError("validation must be short descriptive text.")
    return result


def normalize_context(value: int) -> int:
    return ((value + 255) // 256) * 256


def load_registry(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StackError(f"Cannot read model registry: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version", data.get("version", 1)) != 1 or not isinstance(data.get("models"), list):
        raise StackError("Registry requires schema_version: 1 and a models list.")
    # Checked-in engine paths are portable relative to the repository root, not the shell CWD.
    root = Path(path).resolve().parent.parent
    resolved = []
    for raw in data["models"]:
        model = deepcopy(raw)
        for field in ("engine", "path", "mmproj"):
            if model.get(field) and not Path(model[field]).is_absolute():
                model[field] = str((root / model[field]).resolve())
        resolved.append(validate_model(model, trusted_registry=True))
    data["models"] = resolved
    ids = [model["id"] for model in data["models"]]
    if len(set(ids)) != len(ids):
        raise StackError("Model ids must be unique.")
    return data


def list_models(registry: dict) -> list[dict]:
    return deepcopy(registry["models"])


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
