"""Isolated supervisor tests; never start inference or production proxies."""
import importlib.util
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_model_supervisor', ROOT / 'scripts/windows/local_model_supervisor.py')
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


class SupervisorTests(unittest.TestCase):
    def test_venv_worker_binding_keeps_launcher_and_actual_identity(self):
        launcher = {'name': 'gateway', 'pid': 10, 'identity': {'created': '100', 'executable': 'venv/python.exe'}}
        worker = {'pid': 20, 'identity': {'created': '101', 'executable': 'base/python.exe'}}
        record = {'generation': 'g1', 'children': [launcher]}
        from unittest.mock import Mock
        job = Mock()
        job.contains.return_value = True
        supervisor.bind_gateway_worker(record, launcher, {'supervisor_generation': 'g1', 'gateway_process': worker}, job, identify=lambda pid: worker['identity'], descendant=lambda child, parent: child == worker and parent == launcher, listener=lambda port: worker)
        self.assertEqual(record['children'][0]['name'], 'gateway-launcher')
        self.assertEqual(record['children'][1], {'name': 'gateway', **worker})
        self.assertEqual(record['gateway_process'], worker)

    def test_listener_alone_or_unrelated_job_cannot_bind_gateway_worker(self):
        from unittest.mock import Mock
        launcher = {'name': 'gateway', 'pid': 10, 'identity': {'created': '100'}}
        worker = {'pid': 20, 'identity': {'created': '101'}}
        for ancestry, membership, generation in ((False, True, 'g1'), (True, False, 'g1'), (True, True, 'old')):
            with self.subTest(ancestry=ancestry, membership=membership, generation=generation):
                record = {'generation': 'g1', 'children': [dict(launcher)]}
                job = Mock()
                job.contains.return_value = membership
                with self.assertRaises(RuntimeError):
                    supervisor.bind_gateway_worker(record, record['children'][0], {'supervisor_generation': generation, 'gateway_process': worker}, job, identify=lambda pid: worker['identity'], descendant=lambda child, parent: ancestry, listener=lambda port: worker)
                self.assertEqual(len(record['children']), 1)

    @unittest.skipUnless(os.name == 'nt' and (supervisor.DEPS / 'tui-rich15.0.0/Scripts/python.exe').is_file(), 'Windows staged venv redirector ownership')
    def test_real_venv_redirector_worker_ancestry_job_and_listener_binding(self):
        from local_stack.runtime import process_identity, verified_descendant, listener_process
        python = supervisor.DEPS / 'tui-rich15.0.0/Scripts/python.exe'
        with tempfile.TemporaryDirectory(prefix='watson-worker-identity-') as temp:
            report = Path(temp) / 'worker.json'
            program = "import os,socket,time,json,pathlib,sys; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'port':s.getsockname()[1]})); time.sleep(30)"
            job = supervisor.WindowsJob()
            child = subprocess.Popen([str(python), '-B', '-c', program, str(report)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x00000004 | subprocess.CREATE_NO_WINDOW)
            try:
                launcher = {'pid': child.pid, 'identity': process_identity(child.pid)}
                job.assign(child)
                supervisor.resume_windows_process(child.pid)
                deadline = time.monotonic() + 5
                actual = None
                while time.monotonic() < deadline:
                    try:
                        actual = json.loads(report.read_text())
                        break
                    except (OSError, ValueError):
                        time.sleep(0.01)
                self.assertIsNotNone(actual)
                worker = {'pid': actual['pid'], 'identity': process_identity(actual['pid'])}
                self.assertTrue(verified_descendant(worker, launcher))
                self.assertTrue(job.contains(worker))
                self.assertEqual(listener_process(actual['port']), worker)
            finally:
                job.close()
                child.wait(timeout=10)

    def test_generation_bound_stop_intent_needs_mac_and_live_identities(self):
        record = {'pid': 10, 'identity': {'created': 'a'}, 'generation': 'g1', 'children': [{'name': 'gateway', 'pid': 20, 'identity': {'created': 'b'}}]}
        payload = {'version': 1, 'generation': 'g1', 'supervisor_pid': 10, 'supervisor_identity': {'created': 'a'}, 'gateway_pid': 20, 'gateway_identity': {'created': 'b'}}
        key = 'k' * 48
        def signed(value):
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
            return {'payload': value, 'mac': hmac.new(key.encode(), encoded, 'sha256').hexdigest()}
        identify = lambda pid: {10: {'created': 'a'}, 20: {'created': 'b'}}.get(pid)
        self.assertTrue(supervisor.stop_intent_matches(record, key, signed(payload), identify))
        self.assertFalse(supervisor.stop_intent_matches(record, 'wrong-key', signed(payload), identify))
        self.assertFalse(supervisor.stop_intent_matches(record, key, signed({**payload, 'generation': 'old'}), identify))
        self.assertFalse(supervisor.stop_intent_matches(record, key, signed(payload), lambda pid: {'created': 'reused'}))
        self.assertFalse(supervisor.stop_intent_matches(record, key, {'payload': payload, 'mac': None}, identify))

    def test_custom_registry_and_state_are_forwarded_only_to_gateway(self):
        commands = supervisor.service_commands(True, Path('custom-registry.json'))
        self.assertIn('custom-registry.json', commands[0][1])
        self.assertIn('--state-dir', commands[0][1])

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

    def test_isolated_proxy_commands_set_twenty_minute_read_timeouts(self):
        commands = {name: argv for name, argv, _, _ in supervisor.service_commands()}
        headroom = commands['headroom']
        self.assertIn('--request-timeout-seconds', headroom)
        self.assertEqual(headroom[headroom.index('--request-timeout-seconds') + 1], '1200')
        self.assertEqual(headroom[headroom.index('--retry-max-attempts') + 1], '1')
        self.assertNotIn('--connect-timeout-seconds', headroom)
        self.assertNotIn('--write-timeout-seconds', headroom)
        litellm = commands['litellm']
        path = Path(litellm[litellm.index('--config') + 1])
        self.assertEqual(path, ROOT / 'config' / 'server' / 'local-model-litellm.yaml')
        config = path.read_text(encoding='utf-8')
        self.assertRegex(config, r'(?m)^litellm_settings:\n(?:  [^\n]*\n)*  request_timeout: 1200$')
        self.assertRegex(config, r'(?m)^router_settings:\n(?:  [^\n]*\n)*  stream_timeout: 1200$')
        self.assertRegex(config, r'(?m)^router_settings:\n(?:  [^\n]*\n)*  num_retries: 0$')

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
