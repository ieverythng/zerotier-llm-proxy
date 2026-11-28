"""Observed, identity-checked lifecycle for the complete supervised local stack.

Start/attach and stop are explicit actions. Constructing this module or observing
status never starts services, and closing a terminal never stops its stack.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from .client import StackClient
from .registry import StackError, load_registry, validate_model
from .runtime import ModelRuntime, process_identity

ROOT = Path(__file__).resolve().parents[1]
PORTS = (4141, 18081, 18082, 18083)


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.15)
        return connection.connect_ex(("127.0.0.1", port)) == 0


class StackLifecycle:
    def __init__(self, client=None, registry_path=None, base_url="http://127.0.0.1:4141", state_dir=None, direct=False, *, identify=process_identity, port_probe=port_open, spawn=subprocess.Popen, clock=time.monotonic, sleep=time.sleep, start_timeout=390, stop_timeout=30):
        self.registry_path = Path(registry_path or ROOT / "config" / "models.json").resolve()
        self.state_dir = Path(state_dir or ROOT / ".runtime" / "local-stack").resolve()
        self.client = client or StackClient(base_url, state_dir=self.state_dir)
        self.direct = direct
        self.identify, self.port_probe, self.spawn = identify, port_probe, spawn
        self.clock, self.sleep = clock, sleep
        self.start_timeout, self.stop_timeout = start_timeout, stop_timeout
        self.action_lock = threading.Lock()

    def _record(self):
        try:
            value = json.loads((self.state_dir / "supervisor.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def _alive(self, entry):
        return bool(isinstance(entry, dict) and type(entry.get("pid")) is int and entry["pid"] > 0 and entry.get("identity") and self.identify(entry["pid"]) == entry["identity"])

    def _occupied(self):
        return [port for port in PORTS if self.port_probe(port)]

    def _offline_models(self):
        """Read-only catalog merge: opening the selector does not create runtime state."""
        try:
            registry = load_registry(self.registry_path)
            models = registry['models']
            path = self.state_dir / 'overrides.json'
            if not path.exists():
                return models
            overrides = json.loads(path.read_text(encoding='utf-8'))
            trusted = {os.path.normcase(str(Path(model['engine']).resolve())) for model in models}
            registered = [validate_model(model, trusted_engines=trusted) for model in overrides.get('registered', [])]
            ids = [model['id'] for model in models + registered]
            if len(ids) != len(set(ids)):
                raise StackError('Duplicate offline registrations.')
            models += registered
            for model in models:
                if model['id'] in overrides.get('models', {}):
                    context = ModelRuntime._context(model, overrides['models'][model['id']])
                    if context != model['context']:
                        model.update(context_verified=False, validation='provisional context override')
                    model['context'] = context
            return models
        except (StackError, OSError, ValueError, TypeError, KeyError):
            return []

    def status(self):
        models = self._offline_models()
        record = self._record()
        if not record or not self._alive(record):
            occupied = self._occupied()
            return {"state": "unknown" if occupied else "stopped", "running": False, "owned": False, "busy": False, "message": "Unverified listeners occupy stack ports; nothing will be adopted." if occupied else "Stack stopped. Select a profile, then Start / Attach.", "occupied_ports": occupied, "models": models}
        if not record.get("generation") or record.get("stop_protocol") != 1:
            return {"state": "unknown", "running": False, "owned": False, "busy": False, "message": "A legacy supervisor is running without the verified lifecycle protocol; no process will be adopted or stopped."}
        state = record.get("status", "unknown")
        if state not in {'starting', 'ready', 'stopping', 'stopped'}:
            state = 'unknown'
        base = {"state": "running" if state == "ready" else state, "running": state == "ready", "owned": True, "busy": state in {"starting", "stopping"}, "generation": record["generation"], "direct": record.get("direct", False), "registry_path": record.get("registry_path"), "message": "Stack " + state + ".", "models": models}
        if state != "ready":
            return base
        children = record.get("children", [])
        if not isinstance(children, list) or not all(isinstance(entry, dict) for entry in children):
            return {**base, 'state': 'unknown', 'running': False, 'message': 'Supervisor child identities are malformed.'}
        gateway = next((entry for entry in children if entry.get("name") == "gateway"), None)
        if not gateway or not all(self._alive(entry) for entry in children):
            return {**base, "state": "unknown", "running": False, "message": "A supervised process identity is missing or changed."}
        try:
            observed = self.client.status()
            if observed.get("supervisor_generation") != record["generation"]:
                raise StackError("Gateway generation does not match supervisor ownership.", 409)
            sealed = observed.get('admission_state') == 'stopping'
            return {**observed, **base, "models": observed.get('models', models), "state": 'stopping' if sealed else base['state'], "running": not sealed, "busy": sealed or observed.get("busy", False), "gateway_status": observed}
        except StackError:
            return {**base, "state": "unknown", "running": False, "message": "Owned supervisor is present but its authenticated gateway could not be verified."}

    def start(self, model=None, context=None, direct=None):
        if not self.action_lock.acquire(blocking=False):
            raise StackError("A lifecycle action is already running.", 409)
        try:
            mode = self.direct if direct is None else direct
            if not isinstance(mode, bool):
                raise StackError('Route mode must be boolean.')
            observed = self.status()
            attached = observed["state"] == "running"
            if attached:
                if observed.get("direct", False) != mode:
                    raise StackError("The running stack has a different route mode; stop it explicitly before changing modes.", 409)
                if observed.get("registry_path") and Path(observed["registry_path"]).resolve() != self.registry_path:
                    raise StackError("The running stack uses a different model registry; nothing was replaced.", 409)
            elif observed["state"] not in {"stopped", "starting"}:
                raise StackError(observed.get("message", "Stack ownership could not be verified."), 409)
            child = None
            if observed["state"] == "stopped":
                python = ROOT / ".runtime" / "local-model-deps" / "tui-rich15.0.0" / "Scripts" / "python.exe"
                script = ROOT / "scripts" / "windows" / "local_model_supervisor.py"
                if not python.is_file() or not script.is_file() or not self.registry_path.is_file():
                    raise StackError("Staged launcher runtime or registry is missing. Run Install-LocalModelRuntime.ps1 first.", 503)
                self.state_dir.mkdir(parents=True, exist_ok=True)
                command = [str(python), "-B", str(script), "--serve", "--registry", str(self.registry_path), "--state-dir", str(self.state_dir)]
                if mode:
                    command.append("--direct")
                flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
                with (self.state_dir / "supervisor.stdout.log").open("ab") as stdout, (self.state_dir / "supervisor.stderr.log").open("ab") as stderr:
                    child = self.spawn(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, creationflags=flags, start_new_session=os.name != "nt")
            if not attached:
                deadline = self.clock() + self.start_timeout
                while self.clock() < deadline:
                    observed = self.status()
                    if observed["state"] == "running":
                        if observed.get("direct", False) != mode:
                            raise StackError("Another supervisor started with a different route mode; nothing was replaced.", 409)
                        break
                    if child is not None and child.poll() is not None:
                        # A concurrent Start might have won the supervisor lock. Attach only if verified.
                        observed = self.status()
                        if observed["state"] == "starting" and observed.get('owned'):
                            child = None
                            attached = True
                            self.sleep(0.2)
                            continue
                        if observed["state"] != "running":
                            raise StackError("Supervisor startup exited; inspect its private local logs.", 503)
                        attached = True
                        break
                    self.sleep(0.2)
                else:
                    return {"status": "uncertain", "state": "unknown", "running": False, "message": "Startup readiness timed out. No process was force-stopped; observe status before retrying."}
            if observed.get('direct', False) != mode or (observed.get('registry_path') and Path(observed['registry_path']).resolve() != self.registry_path):
                raise StackError('The verified stack uses a different registry or route mode; nothing was replaced.', 409)
            result = {"status": "attached" if attached else "started", "state": "running", "running": True, "owned": True, "generation": observed.get("generation")}
            if model is not None:
                result["model"] = self.client.load(model, context)
            return result
        finally:
            self.action_lock.release()

    def stop(self):
        if not self.action_lock.acquire(blocking=False):
            raise StackError("A lifecycle action is already running.", 409)
        try:
            observed = self.status()
            if observed["state"] == "stopped":
                return {"status": "already_stopped", "state": "stopped", "running": False, "message": "Stack is already stopped."}
            if not observed.get("owned") or observed["state"] not in {"running", "stopping"}:
                raise StackError("Stack ownership/readiness is uncertain; nothing was stopped.", 409)
            if observed.get("busy") and observed["state"] != "stopping":
                raise StackError("The stack is busy; Stop Stack refuses active work rather than queueing.", 409)
            record = self._record()
            if not record or record.get("generation") != observed.get("generation") or not self._alive(record):
                raise StackError("Supervisor generation changed; nothing was stopped.", 409)
            if observed["state"] != "stopping":
                try:
                    accepted = self.client.stop_stack(observed["generation"])
                except StackError as exc:
                    if exc.status in {400, 401, 403, 404, 409, 501}:
                        raise
                    # The supervisor can close the gateway before its acknowledgement
                    # reaches us. Observe the captured generation instead of retry-killing.
                    accepted = None
                if accepted is not None and (accepted.get("generation") != record["generation"] or accepted.get("status") != "stopping"):
                    return {"status": "uncertain", "state": "unknown", "running": None, "message": "Stop acknowledgement could not be matched to this supervisor."}
            deadline = self.clock() + self.stop_timeout
            entries = [record] + record.get("children", [])
            while self.clock() < deadline:
                newer = self._record()
                if newer and newer.get("generation") != record["generation"]:
                    return {"status": "uncertain", "state": "unknown", "running": None, "message": "A new supervisor generation appeared during shutdown; it was not stopped."}
                if not any(self._alive(entry) for entry in entries):
                    occupied = self._occupied()
                    if not occupied:
                        return {"status": "stopped", "state": "stopped", "running": False, "message": "All verified supervisor processes and stack listeners are stopped."}
                    return {"status": "uncertain", "state": "unknown", "running": None, "message": "Owned processes stopped, but stack ports are now occupied by unverified listeners; they were not touched.", "occupied_ports": occupied}
                self.sleep(0.1)
            return {"status": "uncertain", "state": "stopping", "running": None, "message": "Complete process/listener exit was not confirmed before the deadline. No force-stop was attempted."}
        finally:
            self.action_lock.release()
