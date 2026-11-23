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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

DEFAULT_URL = "http://127.0.0.1:4141"
DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "config" / "models.json"
START_COMMAND = r".\scripts\windows\Start-LocalModelStack.ps1"
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


@dataclass
class ViewState:
    models: list[dict[str, Any]]
    status: dict[str, Any]
    selected_id: str | None = None
    connected: bool = False
    notice: str = "Connecting to the local gateway…"
    action: str | None = None
    filter_text: str = ""

    @property
    def visible_models(self) -> list[dict[str, Any]]:
        needle = self.filter_text.casefold()
        return [row for row in self.models if needle in (str(row.get("name", "")) + " " + row["id"]).casefold()]

    @property
    def selected(self) -> dict[str, Any] | None:
        rows = self.visible_models
        return next((row for row in rows if row["id"] == self.selected_id), rows[0] if rows else None)

    def move(self, offset: int) -> None:
        rows = self.visible_models
        if rows:
            index = next((i for i, row in enumerate(rows) if row["id"] == self.selected_id), 0)
            self.selected_id = rows[(index + offset) % len(rows)]["id"]


def render_dashboard(state: ViewState, width: int = 110, height: int | None = None):
    """Build one Rich renderable; pure enough for deterministic snapshot tests."""
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    compact = height is not None and height < 36
    brand = Text(" WATSON  /  LOCAL MODELS", style="bold bright_cyan")
    brand.append("\nOne GPU · one active model · safe switching", style="dim")
    badge = "CONNECTED" if state.connected else "OFFLINE"
    header = Table.grid(expand=True)
    header.add_column(ratio=1)
    header.add_column(justify="right")
    header.add_row(brand, Text(badge, style="bold green" if state.connected else "bold yellow"))

    resources = state.status.get("resources") or {}
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
    memory.add_row(Text("VRAM  " + vram_text, style="cyan"), Text("RAM  " + ram_text, style="cyan"), Text(f"GPU  {safe_text(gpu) + '%' if gpu is not None else 'unavailable'}", style="cyan"))

    catalog = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False, padding=(0, 1))
    catalog.add_column("", width=2)
    catalog.add_column("MODEL", ratio=3)
    catalog.add_column("CONTEXT", justify="right")
    catalog.add_column("STATE", justify="right")
    selected = state.selected
    selected_id = selected["id"] if selected else None
    active = state.status.get("active_model")
    for row in state.visible_models:
        is_selected = row["id"] == selected_id
        is_active = row["id"] == active
        name = Text(safe_text(row.get("name", row["id"])), style="bold bright_white" if is_selected else "white")
        if not compact:
            name.append("\n" + safe_text(row["id"]), style="dim")
        catalog.add_row(
            Text("❯" if is_selected else "", style="bright_cyan"), name,
            Text(format_tokens(row.get("context")), style="bright_cyan" if is_selected else "white"),
            Text("● LOADED" if is_active else "○ STANDBY", style="green" if is_active else "dim"),
            style="on #17333d" if is_selected else "",
        )
    if not state.visible_models:
        catalog.add_row("", Text("No matching models", style="yellow"), "", "")

    details = Table.grid(padding=(0, 1), expand=True)
    details.add_column(style="dim", width=14)
    details.add_column(ratio=1)
    if selected:
        loaded_context = state.status.get("context") if selected["id"] == active else None
        validation = safe_text(selected.get("validation", "unverified"))
        details.add_row("Profile context", Text(format_tokens(selected.get("context")) + " tokens · input + output"))
        details.add_row("Loaded context", Text(format_tokens(loaded_context) + " tokens" if loaded_context else "Not loaded", style="green" if loaded_context else "dim"))
        details.add_row("Evidence", Text(validation, style="yellow" if "unverified" in validation.lower() or "provisional" in validation.lower() else "cyan"))
        details.add_row("Capabilities", Text(" + ".join(safe_text(item) for item in selected.get("input", ["text"]))))
        details.add_row("Output budget", Text(format_tokens(selected.get("max_output_tokens")) + " tokens"))
        details.add_row("Presets", Text("  /  ".join(format_tokens(value) for value in context_presets(selected))))
        if not compact:
            details.add_row("Artifact", Text(safe_text(selected.get("path", "See registry"), 240), overflow="fold"))
    else:
        details.add_row("Add a GGUF", Text("Press A to register a model using an approved architecture profile."))

    activity = state.action or ("Inference active — switching is locked" if state.status.get("busy") else "Ready for selection")
    notice = Text(activity + "\n", style="bold yellow" if state.action or state.status.get("busy") else "bold green")
    notice.append(safe_error(state.notice), style="white")
    if state.status.get("last_error"):
        notice.append("\nLast error: " + safe_error(state.status["last_error"]), style="yellow")
    if state.status.get("metadata_refresh_required"):
        notice.append("\nDSH catalog needs a metadata refresh after profile changes; see the DSH local-model registration guide.", style="yellow")
    if state.filter_text:
        notice.append("\nFilter: " + safe_text(state.filter_text) + "  (Esc clears)", style="cyan")
    keys = Text(" ↑↓ Select   Enter Load   C Context   A Add GGUF   / Search", style="bold cyan")
    keys.append("\n R Refresh   S Stop owned model   Q Quit (keeps stack running)", style="dim")
    catalog_panel = Panel(catalog, border_style="cyan", title="[bold]MODEL LIBRARY[/bold]")
    details_panel = Panel(details, border_style="dim", title="[dim]SELECTED PROFILE[/dim]")
    if compact and width < 110 and selected:
        summary = Text(f"Profile {format_tokens(selected.get('context'))} ctx · loaded {format_tokens(state.status.get('context')) if selected['id'] == active else '—'} · output {format_tokens(selected.get('max_output_tokens'))}\n")
        summary.append(safe_text(selected.get("validation", "unverified"), max(20, width - 8)), style="dim")
        details_panel = Panel(summary, border_style="dim", title="[dim]SELECTED PROFILE[/dim]")
    if width >= 110:
        split = Table.grid(expand=True, padding=(0, 1))
        split.add_column(ratio=1, width=(width - 2) // 2)
        split.add_column(ratio=1, width=(width - 2) // 2)
        split.add_row(catalog_panel, details_panel)
        content = [split]
    else:
        content = [catalog_panel, details_panel]
    panels = [
        Panel(header, border_style="bright_cyan", padding=(0 if compact else 1, 1)),
        Panel(memory, border_style="dim", title="[dim]LIVE RESOURCES[/dim]"),
        *content,
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


def registration_wizard(console, templates: list[dict[str, Any]], existing_ids: Iterable[str]) -> dict[str, Any] | None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    templates = [row for row in templates if row.get("engine")]
    if not templates:
        raise ValueError("No approved engine profiles are available. Ask the operator to add a reviewed profile first.")
    console.print(Panel(Text("Register a model, not a new engine.\nChoose an existing architecture profile only if it matches the new model.\nNo executable paths or arbitrary flags are accepted here.\nNew artifacts start UNVERIFIED at no more than 8,192 context tokens.\nPress Enter at the first question to cancel."), title="ADD GGUF", border_style="yellow"))
    for index, template in enumerate(templates, 1):
        console.print(Text(f"{index}. {safe_text(template.get('name', template['id']))}  [{safe_text(template.get('architecture', template['id']))}]"))
    choice = _read_choice(console, "Matching architecture profile")
    if not choice:
        return None
    try:
        index = int(choice) - 1
        if not 0 <= index < len(templates):
            raise ValueError
        template = templates[index]
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
    initial = min(8192, context_limit(template))
    context = _read_choice(console, "Initial context tokens", str(initial))
    mmproj = None
    if "image" in (template.get("input") or []):
        console.print(Text("This vision profile requires the projector matching the NEW artifact; the old projector is not assumed compatible.", style="yellow"))
        mmproj = _read_choice(console, "Matching projector GGUF path")
    model = build_registration(template, model_id=model_id, name=name, path=validated, context=context, existing_ids=existing_ids, mmproj=mmproj)
    console.print(Panel(Text(f"{model['name']}\nId: {model['id']}\nArtifact: {model['path']}\nContext: {model['context']:,} (unverified)\nEngine/settings inherited from: {template['id']}\nRegistration does not load or validate the model."), border_style="cyan"))
    return model if _read_choice(console, "Type REGISTER to save") == "REGISTER" else None


def _fetch(client) -> tuple[Any, dict[str, Any]]:
    status = client.status()
    return client.models(), status


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
    console=None,
    refresh_seconds: float = 2.0,
    snapshot: bool = False,
) -> int:
    """Run the terminal UI; dependency injection keeps network/GPU out of tests."""
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text
    except ImportError:
        print("The terminal UI needs Rich. Run the approved local-stack dependency installer first.", file=sys.stderr)
        return 2
    if client is None:
        from .client import StackClient
        client = StackClient(base_url)
    if console is None:
        # Windows Python may inherit cp1252 even inside Windows Terminal.
        # Unicode selection/status symbols must not crash the launcher.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
        console = Console()
    state = ViewState(models=read_registry(registry_path), status={})
    state.selected_id = state.models[0]["id"] if state.models else None
    try:
        catalog, status = _fetch(client)
        state.models = normalize_models(catalog, state.models, status)
        state.status, state.connected = status, True
        state.notice = "Select a model, then press Enter. DSH can also load it on the first request."
    except Exception as exc:
        state.notice = f"Gateway unavailable: {safe_error(exc)}. Start it with {START_COMMAND}"
    if snapshot or not sys.stdin.isatty() or not console.is_terminal:
        console.print(render_dashboard(state, console.width, console.height))
        if not snapshot:
            console.print(Text("Interactive keyboard controls require a real terminal. No state was changed.", style="yellow"))
        return 0 if state.connected else 1

    pending_poll: Future | None = None
    pending_action: Future | None = None
    next_poll = time.monotonic() + refresh_seconds

    def submit(label: str, call: Callable, *args):
        nonlocal pending_action
        if pending_action is not None or state.status.get("busy"):
            state.notice = "An operation or inference is active. Wait; no interrupting switch was sent."
            return
        state.action = label
        state.notice = "The gateway will check ownership, resources and readiness before changing models."
        pending_action = _background(call, *args)

    try:
        with KeyReader() as keys, Live(render_dashboard(state, console.width, console.height), console=console, screen=True, auto_refresh=False) as live:
            while True:
                now = time.monotonic()
                if pending_poll is not None and pending_poll.done():
                    try:
                        catalog, status = pending_poll.result()
                        state.models = normalize_models(catalog, read_registry(registry_path), status)
                        state.status, state.connected = status, True
                    except Exception as exc:
                        state.connected = False
                        state.notice = f"Gateway unavailable: {safe_error(exc)}. Start it with {START_COMMAND}"
                    pending_poll = None
                if pending_action is not None and pending_action.done():
                    try:
                        result = pending_action.result()
                        state.notice = "Request completed. Refreshing authoritative runtime state."
                        if isinstance(result, dict) and result.get("message"):
                            state.notice = safe_text(result["message"])
                    except Exception as exc:
                        state.notice = "Request refused/failed: " + safe_error(exc)
                    pending_action, state.action = None, None
                    next_poll = 0
                if now >= next_poll and pending_poll is None:
                    pending_poll = _background(_fetch, client)
                    next_poll = now + max(0.5, refresh_seconds)
                live.update(render_dashboard(state, console.width, console.height), refresh=True)
                key = keys.read()
                if key in ("q", "quit"):
                    break
                if key in ("up", "k"):
                    state.move(-1)
                elif key in ("down", "j"):
                    state.move(1)
                elif key == "escape":
                    state.filter_text = ""
                elif key == "r":
                    next_poll = 0
                elif key in ("enter", "l") and state.selected:
                    submit("Loading " + safe_text(state.selected["name"]) + "…", client.load, state.selected["id"], state.selected.get("context"))
                elif key in ("c", "a", "s", "/"):
                    if (pending_action is not None or state.status.get("busy")) and key != "/":
                        state.notice = "An operation or inference is active. No changes were sent."
                        continue
                    live.stop()
                    try:
                        with keys.cooked():
                            if key == "c" and state.selected:
                                choice = choose_context(console, state.selected)
                                if choice is not None:
                                    submit("Configuring context…", client.configure, state.selected["id"], choice)
                            elif key == "a":
                                model = registration_wizard(console, state.models, [row["id"] for row in state.models])
                                if model:
                                    submit("Registering model profile…", client.register, model)
                            elif key == "s":
                                answer = _read_choice(console, "Type STOP to unload only the gateway-owned model; proxies remain running")
                                if answer == "STOP":
                                    submit("Stopping the owned model…", client.stop)
                            elif key == "/":
                                state.filter_text = _read_choice(console, "Filter models (blank clears)")
                    except (ValueError, OSError) as exc:
                        state.notice = safe_error(exc)
                    finally:
                        live.start()
                time.sleep(0.06)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        # Do not cancel a started remote load or terminate its owned process.
        # Daemon request threads do not hold the terminal open on exit; the
        # separate gateway retains authority over any request already admitted.
        if pending_poll is not None:
            pending_poll.cancel()
    console.print(Text("Selector closed. Stack and any active request were left running.", style="cyan"))
    return 0
