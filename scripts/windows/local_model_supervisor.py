"""Own an isolated local inference stack; never adopt or terminate unknown listeners."""
from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.runtime' / 'local-stack'
DEPS = ROOT / '.runtime' / 'local-model-deps'
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ENV_ALLOW = {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
             'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE', 'COMSPEC',
             'USERDOMAIN', 'USERNAME', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'CUDA_PATH'}
sys.path.insert(0, str(ROOT))


def isolated_environment(source=None):
    source = os.environ if source is None else source
    env = {key: value for key, value in source.items() if key.upper() in ENV_ALLOW}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               HEADROOM_TELEMETRY='off', HEADROOM_STATELESS='true', HEADROOM_TOOL_INJECTION_STICKY='disabled',
               HEADROOM_RETRY_MAX_ATTEMPTS='1', HEADROOM_NO_MEMORY_TOOLS='true', HEADROOM_NO_MEMORY_CONTEXT='true',
               DO_NOT_TRACK='1', LITELLM_TELEMETRY='false')
    return env


def secure_state_directory():
    STATE.mkdir(parents=True, exist_ok=True)
    if STATE.is_symlink():
        raise RuntimeError('Refusing a symbolic-link local stack state directory.')
    if os.name == 'nt':
        account = os.environ.get('USERDOMAIN', '.') + '\\' + getpass.getuser()
        result = subprocess.run(['icacls.exe', str(STATE), '/inheritance:r', '/grant:r', account + ':(OI)(CI)(F)',
                                 '/grant:r', 'SYSTEM:(OI)(CI)(F)', '/T', '/C'], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('Could not protect private stack state and logs.')
    else:
        STATE.chmod(0o700)


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def require_free_ports(direct=False):
    # A legacy model can otherwise consume the GPU without appearing in our isolated ports.
    ports = [8080, 18081, 4141] + ([] if direct else [18082, 18083])
    occupied = [port for port in ports if port_open(port)]
    if occupied:
        raise RuntimeError('Refusing to adopt or replace occupied ports: ' + ', '.join(map(str, occupied)))


def read_json(url, token=None):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token} if token else {})
    with HTTP.open(request, timeout=3) as response:
        return json.load(response)


class WindowsJob:
    """Kill supervisor-owned descendants when its process exits, including forced exits."""
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_process_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority_class', wintypes.DWORD),
                        ('scheduling_class', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory_limit', ctypes.c_size_t),
                        ('job_memory_limit', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                        ('peak_job_memory', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise RuntimeError('Could not create a Windows supervision job.')
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise RuntimeError('Could not configure descendant cleanup; refusing unsupervised startup.')

    def assign(self, child):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            child.terminate()
            child.wait(timeout=10)
            raise RuntimeError('Could not supervise a child process; startup aborted.')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def resume_windows_process(pid):
    """Resume the primary thread only after the suspended child joins our Job."""
    if os.name != 'nt':
        return
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('thread_id', wintypes.DWORD),
                    ('owner_pid', wintypes.DWORD), ('priority', wintypes.LONG), ('priority_delta', wintypes.LONG),
                    ('flags', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise RuntimeError('Could not enumerate the supervised child thread.')
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.owner_pid == pid:
                thread = kernel.OpenThread(0x0002, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                if not thread:
                    raise RuntimeError('Could not resume a supervised child thread.')
                try:
                    if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                        raise RuntimeError('Could not resume a supervised child process.')
                finally:
                    kernel.CloseHandle(thread)
                return
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise RuntimeError('The supervised child primary thread was not found.')
    finally:
        kernel.CloseHandle(snapshot)


class SupervisorLock:
    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.file = (STATE / 'supervisor.lock').open('a+b')
        self.file.seek(0)
        if self.file.read(1) == b'':
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError('Another local stack supervisor holds the startup lock.') from exc
        return self

    def __exit__(self, *args):
        self.file.close()


def service_commands(direct=False):
    tui_python = DEPS / 'tui-rich15.0.0' / 'Scripts' / 'python.exe'
    gateway = [str(tui_python), '-B', '-m', 'local_stack', 'serve', '--registry', str(ROOT / 'config' / 'models.json'),
               '--port', '4141', '--backend-port', '18081', '--upstream',
               'http://127.0.0.1:18081/v1' if direct else 'http://127.0.0.1:18083/v1']
    if direct:
        return [('gateway', gateway, 'http://127.0.0.1:4141/health', None)]
    litellm = [str(DEPS / 'litellm1.110.2' / 'Scripts' / 'python.exe'), '-B', '-c',
               'from litellm_cli.main import proxy_server; proxy_server()', '--config',
               str(ROOT / 'config' / 'server' / 'local-model-litellm.yaml'), '--host', '127.0.0.1', '--port', '18082']
    headroom = [str(DEPS / 'headroom0.45.0' / 'Scripts' / 'python.exe'), '-B', '-c',
                'from headroom.cli.main import main; main()', 'proxy', '--host', '127.0.0.1', '--port', '18083',
                '--mode', 'token', '--no-cache', '--stateless', '--no-subscription-tracking', '--no-telemetry',
                '--no-memory-tools', '--no-memory-context', '--no-learn', '--disable-kompress',
                '--disable-kompress-fallback', '--no-code-aware', '--retry-max-attempts', '1']
    return [('litellm', litellm, 'http://127.0.0.1:18082/v1/models', 'LITELLM_MASTER_KEY'),
            ('headroom', headroom, 'http://127.0.0.1:18083/health', None),
            ('gateway', gateway, 'http://127.0.0.1:4141/health', None)]


def check_ready(direct=None):
    """Passive health only, and only a supervisor whose PID identity still matches."""
    from local_stack.runtime import process_identity
    try:
        record = json.loads((STATE / 'supervisor.json').read_text(encoding='utf-8'))
        if record.get('status') != 'ready' or process_identity(record['pid']) != record['identity']:
            return False
        if direct is not None and record['direct'] != direct:
            return False
        health = read_json('http://127.0.0.1:4141/health')
        return health.get('gateway') == 'watson-local'
    except (OSError, ValueError, KeyError):
        return False


def run(direct=False):
    from local_stack.gateway import private_token
    from local_stack.registry import atomic_json
    from local_stack.runtime import process_identity
    with SupervisorLock():
        require_free_ports(direct)
        secure_state_directory()
        env = isolated_environment()
        env['LOCAL_STACK_API_KEY'] = private_token(STATE / 'inference.token')
        env['LLAMA_CPP_API_KEY'] = secrets.token_urlsafe(48)
        env['LITELLM_MASTER_KEY'] = 'sk-' + secrets.token_urlsafe(48)
        env['HEADROOM_PROXY_TOKEN'] = secrets.token_urlsafe(48)
        env['OPENAI_TARGET_API_URL'] = 'http://127.0.0.1:18082/v1'
        env['OPENAI_TARGET_API_HEADERS'] = json.dumps({'Authorization': 'Bearer ' + env['LITELLM_MASTER_KEY']})
        commands = service_commands(direct)
        for name, command, _, _ in commands:
            if not Path(command[0]).is_file():
                raise RuntimeError('Missing staged ' + name + ' runtime; run Install-LocalModelRuntime.ps1 first.')
        children, logs = [], []
        job = WindowsJob()
        record = {'pid': os.getpid(), 'identity': process_identity(os.getpid()), 'direct': direct, 'status': 'starting'}
        atomic_json(STATE / 'supervisor.json', record)
        shutdown = False

        def request_shutdown(signum, frame):
            nonlocal shutdown
            shutdown = True

        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)
        try:
            for name, command, url, auth_name in commands:
                log = (STATE / (name + '.log')).open('ab', buffering=0)
                logs.append(log)
                flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | (0x00000004 if os.name == 'nt' else 0)  # CREATE_SUSPENDED
                child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         creationflags=flags)
                children.append((name, child))
                job.assign(child)
                resume_windows_process(child.pid)
                deadline = time.monotonic() + 120
                while True:
                    if shutdown:
                        raise KeyboardInterrupt
                    if any(process.poll() is not None for _, process in children):
                        raise RuntimeError(name + ' exited during startup; inspect private local stack logs.')
                    try:
                        read_json(url, env.get(auth_name) if auth_name else None)
                        break
                    except (OSError, ValueError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError(name + ' failed its passive readiness check within 120 seconds.')
                        time.sleep(0.25)
            record['status'] = 'ready'
            atomic_json(STATE / 'supervisor.json', record)
            print('Watson Local ready at http://127.0.0.1:4141/v1 (' + ('direct diagnostic' if direct else 'Headroom + LiteLLM') + ').', flush=True)
            while not shutdown:
                if any(process.poll() is not None for _, process in children):
                    raise RuntimeError('A supervised process exited; closing only this stack.')
                time.sleep(0.5)
        finally:
            record['status'] = 'stopped'
            atomic_json(STATE / 'supervisor.json', record)
            # Closing the Job kills its children and their descendants, including any loaded llama worker.
            job.close()
            for _, child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            for log in logs:
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='Run foreground supervision until explicitly stopped.')
    parser.add_argument('--direct', action='store_true', help='Diagnostic gateway-to-model route without proxies.')
    parser.add_argument('--check', action='store_true', help='Exit zero only for this ready supervised stack.')
    args = parser.parse_args()
    if args.check:
        return 0 if check_ready(args.direct) else 1
    if not args.serve:
        parser.error('Pass --serve or --check.')
    try:
        run(args.direct)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Do not print subprocess argv, environment, token content or arbitrary upstream error bodies.
        print('Local stack startup stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
