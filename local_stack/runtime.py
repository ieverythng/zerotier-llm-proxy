"""Owned-process model lifecycle. Public methods share one nonblocking admission lock."""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .registry import StackError, atomic_json, load_registry, normalize_context, resource_limits, validate_model


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "Redirects are disabled for authenticated local traffic.", headers, response)


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _system_cpu_times() -> tuple[int, int] | None:
    """Return cumulative (idle, total) system CPU time, without delays or processes.

    Windows kernel time already includes idle; Linux guest counters are already
    included in user/nice. Do not double-count either when forming the total.
    On Windows systems with >64 CPUs, GetSystemTimes is group-scoped, so report
    unavailable rather than label one processor group as a whole-machine value.
    """
    try:
        if os.name == 'nt':
            count = os.cpu_count()
            if count is None or count > 64:
                return None
            idle, kernel, user = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
            api = ctypes.windll.kernel32.GetSystemTimes
            api.argtypes = [ctypes.POINTER(ctypes.c_uint64)] * 3
            api.restype = ctypes.c_int
            if not api(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return idle.value, kernel.value + user.value
        with Path('/proc/stat').open('r', encoding='ascii') as source:
            fields = source.readline().split()
        if not fields or fields[0] != 'cpu' or len(fields) < 5:
            return None
        ticks = [int(value) for value in fields[1:9]]
        if any(value < 0 for value in ticks):
            return None
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        return idle, sum(ticks)
    except (OSError, ValueError, AttributeError):
        return None


class AggregateCpuSampler:
    """Thread-safe CPU-time deltas. First/duplicate/reset/invalid readings are gaps.

    This only observes counters when an existing resource snapshot is collected;
    it never starts a sampler, sleeps, runs a command or affects admission rules.
    """
    def __init__(self, reader=_system_cpu_times, clock=time.monotonic):
        self.reader, self.clock = reader, clock
        self.lock = threading.Lock()
        self.previous = None

    def sample(self):
        with self.lock:
            try:
                counters = self.reader()
                now = self.clock()
                valid = isinstance(counters, tuple) and len(counters) == 2 and all(type(value) is int and value >= 0 for value in counters) and counters[0] <= counters[1]
                if not valid or type(now) not in (int, float) or not math.isfinite(now):
                    self.previous = None
                    return {'cpu_utilization_percent': None, 'cpu_interval_seconds': None}
                idle, total = counters
                previous = self.previous
                if previous is None:
                    self.previous = (idle, total, now)
                    return {'cpu_utilization_percent': None, 'cpu_interval_seconds': None}
                previous_idle, previous_total, previous_time = previous
                idle_delta, total_delta = idle - previous_idle, total - previous_total
                if idle_delta < 0 or total_delta < 0 or idle_delta > total_delta or now <= previous_time:
                    self.previous = (idle, total, now)
                    return {'cpu_utilization_percent': None, 'cpu_interval_seconds': None}
                if total_delta == 0:
                    # Preserve the last advancing baseline. A duplicate is not
                    # a new zero-utilization observation or a new interval.
                    return {'cpu_utilization_percent': None, 'cpu_interval_seconds': None}
                self.previous = (idle, total, now)
                busy = (total_delta - idle_delta) * 100.0 / total_delta
                return {'cpu_utilization_percent': round(busy, 3), 'cpu_interval_seconds': round(now - previous_time, 6)}
            except Exception:
                self.previous = None
                return {'cpu_utilization_percent': None, 'cpu_interval_seconds': None}


_CPU_SAMPLER = AggregateCpuSampler()


def _memory_snapshot():
    if os.name == 'nt':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ('total_phys', 'avail_phys', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'avail_extended')]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise StackError('Windows memory telemetry is unavailable.', 503)
        available, total = memory.avail_phys / (1024 ** 3), memory.total_phys / (1024 ** 3)
    else:
        entries = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(entries['MemAvailable'].strip().split()[0]) / (1024 ** 2)
        raw_total = entries.get('MemTotal')
        total = int(raw_total.strip().split()[0]) / (1024 ** 2) if raw_total else None
    return available, total if total is not None and total > 0 and total >= available else None


def resource_snapshot() -> dict:
    """Fail closed on existing required RAM/VRAM/utilization; CPU/capacity optional.

    CPU is an interval ending at collection; capacities and free-memory readings
    are system-wide gauges. No additional process is spawned for these fields.
    """
    ram, total_ram = _memory_snapshot()
    try:
        output = subprocess.run(['nvidia-smi', '--query-gpu=memory.free,memory.total,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        values = output.stdout.strip().splitlines()
        if len(values) != 1:
            raise ValueError('Expected exactly one GPU')
        fields = [value.strip() for value in values[0].split(',')]
        if len(fields) != 3:
            raise ValueError('Expected free, total, utilization')
        free, utilization = int(fields[0]), int(fields[2])
        if free < 0 or not 0 <= utilization <= 100:
            raise ValueError('Invalid required GPU observation')
        try:
            total_vram = int(fields[1])
            if total_vram <= 0 or free > total_vram:
                total_vram = None
        except ValueError:
            total_vram = None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise StackError('Single-GPU NVIDIA telemetry is unavailable; refusing a blind model load.', 503) from exc
    return {'available_ram_gib': round(ram, 3), 'free_vram_mib': free, 'gpu_utilization_percent': utilization, 'total_ram_gib': round(total_ram, 3) if total_ram is not None else None, 'total_vram_mib': total_vram, **_CPU_SAMPLER.sample()}


def _windows_identity(handle) -> dict | None:
    kernel = ctypes.windll.kernel32
    length = ctypes.c_ulong(32768)
    path = ctypes.create_unicode_buffer(length.value)
    if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
        return None
    created, exited, system, user = (ctypes.c_ulonglong() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
        return None
    return {"executable": os.path.normcase(str(Path(path.value).resolve())), "created": str(created.value)}


def process_identity(pid: int) -> dict | None:
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            return _windows_identity(ctypes.c_void_p(handle))
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        return {"executable": str(Path(f"/proc/{pid}/exe").resolve()), "created": Path(f"/proc/{pid}/stat").read_text().split()[21]}
    except OSError:
        return None


def process_parent_pid(pid: int) -> int | None:
    """Read process ancestry without exposing command lines or environment secrets."""
    if os.name != 'nt':
        try:
            return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD), ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return None
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.pid == pid:
                return int(entry.parent)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        kernel.CloseHandle(snapshot)


def verified_descendant(child: dict, ancestor: dict, *, identify=process_identity, parent_of=process_parent_pid) -> bool:
    """Bind every ancestry hop by creation time, rejecting recycled parent PIDs."""
    try:
        if identify(child['pid']) != child['identity'] or identify(ancestor['pid']) != ancestor['identity']:
            return False
        current = child
        seen = set()
        for _ in range(32):
            if current['pid'] == ancestor['pid']:
                return current['identity'] == ancestor['identity'] and identify(child['pid']) == child['identity'] and identify(ancestor['pid']) == ancestor['identity']
            if current['pid'] in seen:
                return False
            seen.add(current['pid'])
            parent = parent_of(current['pid'])
            identity = identify(parent) if parent else None
            if not identity or int(identity['created']) > int(current['identity']['created']) or identify(current['pid']) != current['identity']:
                return False
            current = {'pid': parent, 'identity': identity}
        return False
    except (KeyError, TypeError, ValueError):
        return False


def listener_process(port: int) -> dict | None:
    """Return the exact IPv4 loopback listener's PID/creation/executable on Windows."""
    if os.name != 'nt':
        return None
    from ctypes import wintypes
    class TcpRow(ctypes.Structure):
        _fields_ = [(field, wintypes.DWORD) for field in ('state', 'local_address', 'local_port', 'remote_address', 'remote_port', 'pid')]
    api = ctypes.WinDLL('iphlpapi', use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.GetExtendedTcpTable.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if api.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0) not in (0, 122):
        return None
    if size.value < ctypes.sizeof(wintypes.DWORD) or size.value > 16 * 1024 * 1024:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0) != 0:
        return None
    count = wintypes.DWORD.from_buffer(buffer).value
    offset = ctypes.sizeof(wintypes.DWORD)
    if offset + count * ctypes.sizeof(TcpRow) > size.value:
        return None
    owners = []
    for index in range(count):
        row = TcpRow.from_buffer(buffer, offset + index * ctypes.sizeof(TcpRow))
        address = socket.inet_ntoa(struct.pack('<I', row.local_address))
        if row.state == 2 and socket.ntohs(row.local_port & 0xffff) == port and address == '127.0.0.1':
            owners.append(int(row.pid))
    if len(owners) != 1:
        return None
    identity = process_identity(owners[0])
    return {'pid': owners[0], 'identity': identity} if identity else None


def terminate_owned(record: dict):
    """Terminate only a process whose executable AND creation time still match."""
    pid = record["pid"]
    expected = record["identity"]
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x100000, False, pid)
        if not handle:
            return
        try:
            if _windows_identity(ctypes.c_void_p(handle)) != expected:
                raise StackError("Owned PID identity changed; refusing to stop it.", 409)
            if not kernel.TerminateProcess(ctypes.c_void_p(handle), 0):
                raise StackError("Could not stop the owned llama process.", 503)
            kernel.WaitForSingleObject(ctypes.c_void_p(handle), 10000)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    else:
        if process_identity(pid) != expected:
            raise StackError("Owned PID identity changed; refusing to stop it.", 409)
        import signal
        os.kill(pid, signal.SIGTERM)


def server_arguments(model: dict, context: int, port: int) -> list[str]:
    """Pure, explicit command planning: no credentials in the argument list."""
    args = [model["engine"], "-m", model["path"], "-a", model["id"], "--host", "127.0.0.1", "--port", str(port), "-c", str(context), "-b", str(model["batch_size"]), "-ub", str(model["ubatch_size"]), "-fa", model.get("flash_attention", "on"), "-ctk", model["kv_cache"], "-ctv", model["kv_cache"], "--jinja", "-np", "1", "-cram", "0", "-lm", model.get("load_mode", "dio"), "--metrics"]
    if model["gpu_layers"] == "auto":
        args += ["--fit", "on", "--fit-target", str(model["fit_target_mib"])]
    else:
        args += ["-ngl", str(model["gpu_layers"]), "--fit", "off"]
    if model.get("mmproj"):
        placement = "--mmproj-offload" if model.get("mmproj_gpu", True) else "--no-mmproj-offload"
        args += ["-mm", model["mmproj"], placement, "--image-min-tokens", str(model["image_min_tokens"])]
    if model.get("reasoning_effort"):
        args += ["--reasoning", model.get("reasoning", "auto"), "--chat-template-kwargs", json.dumps({"reasoning_effort": model["reasoning_effort"]}, separators=(",", ":"))]
    else:
        args += ["--reasoning", model.get("reasoning", "off")]
    if "reasoning_budget" in model:
        args += ["--reasoning-budget", str(model["reasoning_budget"])]
    for field, flag in (("temperature", "--temp"), ("top_p", "--top-p"), ("top_k", "--top-k"), ("min_p", "--min-p")):
        if field in model:
            args += [flag, str(model[field])]
    return args + model["extra_args"]


class ModelRuntime:
    def __init__(self, registry_path, state_dir=None, backend_port=18081, *, resources=resource_snapshot, spawn=subprocess.Popen, identify=process_identity, terminate=terminate_owned, clock=time.monotonic, sleep=time.sleep):
        self.registry_path = Path(registry_path).resolve()
        self.base_registry = load_registry(self.registry_path)
        self.state_dir = Path(state_dir or self.registry_path.parent.parent / ".runtime" / "local-stack").resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.backend_port = backend_port
        self.resources = resources
        self.spawn = spawn
        self.identify = identify
        self.terminate = terminate
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.record = None
        self.child = None
        self.last_error = None
        self.last_resources = None
        self.resources_sampled_at = None
        self.resources_stale = True
        self._resource_sample_time = None
        self.operation = None
        self.admission_state = "open"
        self.supervisor_generation = os.environ.get("LOCAL_STACK_GENERATION")
        self._resource_sample_lock = threading.Lock()
        self.overrides = self._read_state("overrides.json", {"models": {}, "registered": []})
        self._validate_overrides()
        marker = self._read_state("owned-model.json", None)
        if marker and isinstance(marker, dict) and marker.get("pid") and marker.get("identity"):
            if self.identify(marker["pid"]) == marker["identity"]:
                # Only adopt a prior launch when its planned profile still matches the current trusted executable.
                model = self.find_model(marker.get("model"))
                if marker["identity"]["executable"] == os.path.normcase(str(Path(model["engine"]).resolve())):
                    self.record = marker

    def _read_state(self, name, default):
        path = self.state_dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)
        except (OSError, ValueError) as exc:
            raise StackError(f"Invalid runtime state file {name}; inspect it before continuing.", 503) from exc

    def _trusted_engines(self):
        return {os.path.normcase(str(Path(model["engine"]).resolve())) for model in self.base_registry["models"]}

    def _validate_overrides(self):
        if not isinstance(self.overrides, dict) or not isinstance(self.overrides.get("models"), dict) or not isinstance(self.overrides.get("registered"), list):
            raise StackError("Invalid runtime model overrides.", 503)
        self.overrides["registered"] = [validate_model(model, trusted_engines=self._trusted_engines()) for model in self.overrides["registered"]]
        ids = [model["id"] for model in self.base_registry["models"] + self.overrides["registered"]]
        if len(set(ids)) != len(ids):
            raise StackError("Runtime registrations contain duplicate model ids; inspect overrides before serving.", 503)
        for model_id, context in self.overrides["models"].items():
            model = self.find_model(model_id, apply_override=False)
            self._context(model, context)

    def all_models(self):
        with self.state_lock:
            result = deepcopy(self.base_registry["models"] + self.overrides["registered"])
            for model in result:
                if model["id"] in self.overrides["models"]:
                    desired = self._context(model, self.overrides["models"][model["id"]])
                    if desired != model["context"]:
                        model["context_verified"] = False
                        model["validation"] = "provisional context override"
                    model["context"] = desired
            return result

    def find_model(self, model_id, apply_override=True):
        for model in self.base_registry["models"] + self.overrides.get("registered", []):
            if model["id"] == model_id:
                result = deepcopy(model)
                if apply_override and model_id in self.overrides["models"]:
                    result["context"] = self._context(result, self.overrides["models"][model_id])
                return result
        raise StackError("Unknown model id; select a registered model.", 404)

    @staticmethod
    def _context(model, context):
        if isinstance(context, bool) or not isinstance(context, int):
            raise StackError("Context must be an integer.")
        value = normalize_context(context)
        if not model["minimum_context"] <= value <= model["maximum_context"] or value <= model["max_output_tokens"]:
            raise StackError("Context is outside the profile range or leaves no input budget.")
        return value

    def acquire(self, operation):
        if not self.lock.acquire(blocking=False):
            raise StackError("A model operation or inference is already active; retry after it finishes.", 409)
        if self.admission_state != "open":
            self.lock.release()
            raise StackError("The stack is stopping; new work is not admitted.", 503)
        self.operation = operation

    def release(self):
        self.operation = None
        self.lock.release()

    def _refresh_status_resources(self):
        try:
            self.last_resources = self.resources()
            self.resources_sampled_at = datetime.now(timezone.utc).isoformat()
            self.resources_stale = False
            self._resource_sample_time = self.clock()
        except Exception:
            self.resources_stale = True
        finally:
            self._resource_sample_lock.release()

    def status(self):
        # Observation must remain fast even if the GPU driver stalls.
        if self._resource_sample_lock.acquire(blocking=False):
            threading.Thread(target=self._refresh_status_resources, daemon=True).start()
        resources = self.last_resources
        with self.state_lock:
            alive = self.record and self.identify(self.record["pid"]) == self.record["identity"]
            return {"active_model": self.record["model"] if alive else None, "context": self.record["context"] if alive else None, "busy": self.lock.locked(), "operation": self.operation, "resources": resources, "last_error": self.last_error, "models": self.all_models(), "backend_port": self.backend_port, "admission_state": self.admission_state, "supervisor_generation": self.supervisor_generation, "resources_sampled_at": self.resources_sampled_at, "resources_stale": self.resources_stale or self._resource_sample_time is None or self.clock() - self._resource_sample_time > 5}

    def configure(self, model_id, context):
        self.acquire("configure")
        try:
            model = self.find_model(model_id)
            context = self._context(model, context)
            with self.state_lock:
                candidate = deepcopy(self.overrides)
                candidate["models"][model_id] = context
                atomic_json(self.state_dir / "overrides.json", candidate)
                self.overrides = candidate
            return {"model": model_id, "context": context, "applies": "next safe load or inference", "metadata_refresh_required": True}
        finally:
            self.release()

    def register(self, model):
        self.acquire("register")
        try:
            model = validate_model(model, trusted_engines=self._trusted_engines())
            if any(existing["id"] == model["id"] for existing in self.all_models()):
                raise StackError("Model id already exists.", 409)
            for field in ("path", "engine", "mmproj"):
                if model.get(field) and not Path(model[field]).is_file():
                    raise StackError(f"The registered {field} file does not exist.")
            with Path(model["path"]).open("rb") as artifact:
                if artifact.read(4) != b"GGUF":
                    raise StackError("The model file is not a GGUF artifact.")
            model["validation"] = "unverified"
            model["context_verified"] = False
            model.pop("tested_prompt_tokens", None)
            with self.state_lock:
                candidate = deepcopy(self.overrides)
                candidate["registered"].append(model)
                atomic_json(self.state_dir / "overrides.json", candidate)
                self.overrides = candidate
            return {"model": model, "metadata_refresh_required": True}
        finally:
            self.release()

    def _telemetry_gate(self, model, *, startup=False):
        snapshot = self.resources()
        self.last_resources = snapshot
        self.resources_sampled_at = datetime.now(timezone.utc).isoformat()
        self.resources_stale = False
        self._resource_sample_time = self.clock()
        resources = self.base_registry.get("resources", {})
        # A serialized rollback profile cannot opt into a reduced reserve. Match
        # the selected model's artifact/engine/projector against the base registry.
        trusted = next((entry for entry in self.base_registry["models"]
                        if all(entry.get(key) == model.get(key) for key in ("id", "path", "engine", "mmproj"))), None)
        limits = resource_limits(resources, trusted, startup=startup)
        minimum_ram = limits["minimum_available_ram_gib"]
        minimum_vram = limits["minimum_free_vram_mib"] + (model["estimated_vram_mib"] if startup else 0)
        if snapshot["available_ram_gib"] < minimum_ram or snapshot["free_vram_mib"] < minimum_vram:
            raise StackError(f"Resource reserve failed: need {minimum_ram} GiB available RAM and {minimum_vram} MiB free VRAM; have {snapshot['available_ram_gib']} GiB and {snapshot['free_vram_mib']} MiB.", 503)
        if startup and snapshot.get("gpu_utilization_percent", 100) > resources.get("maximum_startup_gpu_utilization_percent", 92):
            raise StackError("GPU is busy; refusing to load a model now.", 503)
        return snapshot

    def _listener_present(self):
        with socket.socket() as connection:
            connection.settimeout(0.3)
            return connection.connect_ex(("127.0.0.1", self.backend_port)) == 0

    def _check_health(self, model, context):
        key = os.environ.get("LLAMA_CPP_API_KEY")
        if not key:
            raise StackError("LLAMA_CPP_API_KEY is required for authenticated model health checks.", 503)
        request = urllib.request.Request(f"http://127.0.0.1:{self.backend_port}/v1/models", headers={"Authorization": "Bearer " + key})
        with HTTP.open(request, timeout=3) as response:
            payload = json.load(response)
        entries = [entry for entry in payload.get("data", []) if entry.get("id") == model["id"]]
        if len(entries) != 1 or entries[0].get("meta", {}).get("n_ctx") != context:
            raise StackError("Model alias or allocated context does not match the requested profile.", 503)

    def _backend_idle(self):
        """Do not unload while a cancelled/proxied request still computes downstream."""
        key = os.environ.get("LLAMA_CPP_API_KEY")
        if not key:
            raise StackError("Backend authentication is required before a safe switch.", 503)
        request = urllib.request.Request(f"http://127.0.0.1:{self.backend_port}/metrics", headers={"Authorization": "Bearer " + key})
        try:
            with HTTP.open(request, timeout=3) as response:
                text = response.read(1024 * 1024).decode("utf-8")
            samples = re.findall(r"^llamacpp:requests_processing(?:\{[^\n]*\})?\s+([0-9.]+)\s*$", text, re.MULTILINE)
            if not samples:
                raise StackError("Backend active-request metric is unavailable; refusing an unsafe switch.", 503)
            if any(float(value) > 0 for value in samples):
                raise StackError("The backend still has an active request; no model was stopped.", 409)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise StackError("Cannot verify the backend is idle; no model was stopped.", 503) from exc

    def _stop_current(self, *, startup_failure=False):
        with self.state_lock:
            if self.record:
                record = self.record
                identity = self.identify(record["pid"])
                if identity and identity != record["identity"]:
                    raise StackError("Owned PID changed identity; no process was stopped.", 409)
                if identity:
                    if not startup_failure:
                        self._backend_idle()
                    self.terminate(record)
                self.record = None
                self.child = None
                marker = self.state_dir / "owned-model.json"
                if marker.exists():
                    marker.unlink()
        deadline = self.clock() + 10
        while self._listener_present() and self.clock() < deadline:
            self.sleep(0.1)
        if self._listener_present():
            raise StackError("Backend port remains occupied; no unknown listener will be stopped.", 409)

    def _start(self, model, context):
        if self._listener_present():
            raise StackError("Backend port is occupied by an unowned listener. Nothing was stopped.", 409)
        for field in ("engine", "path", "mmproj"):
            if model.get(field) and not Path(model[field]).is_file():
                raise StackError(f"Configured {field} file is missing; check the registry.", 503)
        backend_key = os.environ.get("LLAMA_CPP_API_KEY")
        if not backend_key:
            raise StackError("LLAMA_CPP_API_KEY is required; no model was started.", 503)
        self._telemetry_gate(model, startup=True)
        log_dir = self.state_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment["LLAMA_API_KEY"] = backend_key
        environment.pop("LOCAL_STACK_STOP_KEY", None)
        arguments = server_arguments(model, context, self.backend_port)
        with (log_dir / f"{model['id']}.stdout.log").open("ab") as stdout, (log_dir / f"{model['id']}.stderr.log").open("ab") as stderr:
            child = self.spawn(arguments, cwd=str(Path(model["engine"]).parent), env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        identity = self.identify(child.pid)
        if not identity:
            # It was just created by this invocation, so this handle is safe to terminate.
            child.terminate()
            child.wait(timeout=10)
            raise StackError("Could not establish identity of the newly launched model.", 503)
        with self.state_lock:
            self.child = child
            self.record = {"pid": child.pid, "identity": identity, "model": model["id"], "context": context, "profile": deepcopy(model), "ready": False}
            try:
                atomic_json(self.state_dir / "owned-model.json", self.record)
            except OSError as exc:
                self._stop_current(startup_failure=True)
                raise StackError("Cannot persist owned-process identity; the new model was stopped.", 503) from exc
        deadline = self.clock() + model["startup_timeout_seconds"]
        low_resource_samples = 0
        try:
            while self.clock() < deadline:
                if child.poll() is not None:
                    raise StackError(f"Model exited during startup (code {child.returncode}); inspect its local logs.", 503)
                try:
                    self._telemetry_gate(model)
                    low_resource_samples = 0
                except StackError:
                    low_resource_samples += 1
                    if low_resource_samples >= 2:
                        raise StackError("Resource reserve stayed below the safe floor during model loading; the new owned process was stopped.", 503)
                try:
                    self._check_health(model, context)
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    self.sleep(0.5)
            else:
                raise StackError("Model startup timed out; inspect its local logs.", 503)
            self._telemetry_gate(model)
            self.record["ready"] = True
            atomic_json(self.state_dir / "owned-model.json", self.record)
        except Exception:
            self._stop_current(startup_failure=True)
            raise

    def ensure_loaded(self, model_id, context=None):
        """Caller must own admission lock through the complete inference response."""
        model = self.find_model(model_id)
        context = self._context(model, context if context is not None else model["context"])
        current = deepcopy(self.record)
        if current and self.identify(current["pid"]) == current["identity"] and current["model"] == model_id and current["context"] == context:
            self._check_health(model, context)
            self._backend_idle()
            self._telemetry_gate(model)
            return model
        # Validate files before releasing the old resident model.
        for field in ("engine", "path", "mmproj"):
            if model.get(field) and not Path(model[field]).is_file():
                raise StackError(f"Configured {field} file is missing; current model was preserved.", 503)
        self._stop_current()
        try:
            self._start(model, context)
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, StackError) else "Model switch failed; inspect local logs."
            if current and current.get("profile") and not self.record:
                try:
                    self._start(current["profile"], current["context"])
                except Exception:
                    self.last_error += " Previous model could not be restored automatically."
            raise
        self.last_error = None
        return model

    def load(self, model_id, context=None):
        self.acquire("load")
        try:
            model = self.find_model(model_id)
            if context is not None:
                context = self._context(model, context)
            self.ensure_loaded(model_id, context)
            if context is not None:
                with self.state_lock:
                    self.overrides["models"][model_id] = context
                    atomic_json(self.state_dir / "overrides.json", self.overrides)
            return {"active_model": model_id, "context": self.record["context"], "metadata_refresh_required": True}
        finally:
            self.release()

    def shutdown_stack(self, submit_intent):
        """Seal admission and unload only when idle, then signal the verified supervisor.

        After the intent is accepted admission stays sealed even if the HTTP
        response is lost. A client must observe actual shutdown, not retry-kill.
        """
        self.acquire("stop-stack")
        try:
            self.admission_state = "stopping"
            self._stop_current()
            result = submit_intent()
            return result
        except Exception:
            self.admission_state = "open"
            raise
        finally:
            self.release()

    def stop(self):
        self.acquire("stop")
        try:
            self._stop_current()
            return {"active_model": None, "context": None}
        finally:
            self.release()
