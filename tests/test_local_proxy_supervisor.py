"""Isolated supervisor tests; never start inference or production proxies."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_model_supervisor', ROOT / 'scripts/windows/local_model_supervisor.py')
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


class SupervisorTests(unittest.TestCase):
    def test_hosted_credentials_never_inherit(self):
        result = supervisor.isolated_environment({'PATH': 'safe-bin', 'SYSTEMROOT': 'C:/Windows',
            'OPENAI_API_KEY': 'private', 'ANTHROPIC_AUTH_TOKEN': 'private', 'AWS_SECRET_ACCESS_KEY': 'private',
            'OPENAI_TARGET_API_HEADERS': 'private', 'LITELLM_MASTER_KEY': 'old-private'})
        self.assertEqual(result['PATH'], 'safe-bin')
        self.assertFalse(any(value == 'private' or value == 'old-private' for value in result.values()))
        self.assertEqual(result['HEADROOM_STATELESS'], 'true')
        self.assertEqual(result['HEADROOM_TELEMETRY'], 'off')

    def test_unknown_port_refused_without_process_actions(self):
        with patch.object(supervisor, 'port_open', side_effect=lambda port: port == 8080):
            with self.assertRaisesRegex(RuntimeError, '8080'):
                supervisor.require_free_ports()
        with patch.object(supervisor, 'port_open', return_value=False):
            supervisor.require_free_ports()

    def test_commands_isolate_ports_and_no_memory(self):
        commands = supervisor.service_commands()
        self.assertEqual([entry[0] for entry in commands], ['litellm', 'headroom', 'gateway'])
        self.assertIn('18082', commands[0][1])
        self.assertIn('18083', commands[1][1])
        self.assertIn('--stateless', commands[1][1])
        self.assertNotIn('--memory', commands[1][1])
        self.assertIn('http://127.0.0.1:18083/v1', commands[2][1])
        self.assertIn('18081', commands[2][1])
        self.assertTrue(all('local-model-deps' in entry[1][0] for entry in commands))

    def test_direct_mode_never_starts_proxies(self):
        commands = supervisor.service_commands(True)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'gateway')
        self.assertIn('http://127.0.0.1:18081/v1', commands[0][1])

    def test_readiness_refuses_stale_pid_or_mode(self):
        from local_stack import runtime
        with tempfile.TemporaryDirectory(prefix='watson-supervisor-test-') as temp:
            path = Path(temp)
            (path / 'supervisor.json').write_text(json.dumps({'pid': 123, 'identity': {'created': 'old'}, 'status': 'ready', 'direct': False}))
            with patch.object(supervisor, 'STATE', path), patch.object(runtime, 'process_identity', return_value={'created': 'new'}), patch.object(supervisor, 'read_json') as probe:
                self.assertFalse(supervisor.check_ready(False))
                probe.assert_not_called()
            with patch.object(supervisor, 'STATE', path), patch.object(runtime, 'process_identity', return_value={'created': 'old'}), patch.object(supervisor, 'read_json') as probe:
                self.assertFalse(supervisor.check_ready(True))
                probe.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows state-directory ACLs')
    def test_secured_state_can_reopen_launcher_files_on_second_start(self):
        with tempfile.TemporaryDirectory(prefix='watson-acl-test-') as temp:
            state = Path(temp) / 'local-stack'
            state.mkdir()
            paths = [state / 'supervisor.lock', state / 'supervisor.stdout.log', state / 'supervisor.stderr.log']
            for path in paths:
                path.write_bytes(b'x')
            try:
                with patch.object(supervisor, 'STATE', state):
                    for _ in range(2):
                        with supervisor.SupervisorLock():
                            supervisor.secure_state_directory()
                        for path in paths[1:]:
                            with path.open('ab') as log:
                                log.write(b'test')
            finally:
                for path in paths:
                    subprocess.run(['icacls.exe', str(path), '/inheritance:e'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=True)

    @unittest.skipUnless(os.name == 'nt', 'Windows ownership lifecycle')
    def test_suspended_child_is_owned_before_it_can_run_and_killed_on_close(self):
        job = supervisor.WindowsJob()
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=0x00000004 | subprocess.CREATE_NO_WINDOW)
        try:
            job.assign(child)
            supervisor.resume_windows_process(child.pid)
            self.assertIsNone(child.poll())
            job.close()
            child.wait(timeout=10)
            self.assertIsNotNone(child.returncode)
        finally:
            job.close()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
