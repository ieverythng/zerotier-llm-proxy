"""Lifecycle decisions tested with fake identities, HTTP and clocks; no services."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from local_stack.client import StackClient
from local_stack.lifecycle import StackLifecycle
from local_stack.registry import StackError


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.registry = self.root / 'config' / 'models.json'
        self.registry.parent.mkdir()
        self.registry.write_text('{}')
        engine = self.root / '.runtime/local-model-deps/tui-rich15.0.0/Scripts/python.exe'
        engine.parent.mkdir(parents=True)
        engine.touch()
        script = self.root / 'scripts/windows/local_model_supervisor.py'
        script.parent.mkdir(parents=True)
        script.touch()
        self.identities = {}
        self.ports = set()
        self.client = Mock()
        self.client.status.return_value = {'supervisor_generation': 'generation-1', 'admission_state': 'open', 'active_model': None, 'context': None, 'busy': False}
        self.spawn = Mock()
        self.time = 0
        def sleep(seconds):
            self.time += seconds
        self.lifecycle = StackLifecycle(self.client, self.registry, state_dir=self.state, identify=lambda pid: self.identities.get(pid), port_probe=lambda port: port in self.ports, spawn=self.spawn, clock=lambda: self.time, sleep=sleep, start_timeout=1, stop_timeout=1)
        self.root_patch = patch('local_stack.lifecycle.ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def seed_ready(self):
        self.identities.update({1: {'executable': 'supervisor', 'created': 'one'}, 2: {'executable': 'gateway', 'created': 'two'}})
        record = {'pid': 1, 'identity': self.identities[1], 'generation': 'generation-1', 'stop_protocol': 1, 'registry_path': str(self.registry), 'status': 'ready', 'direct': False, 'children': [{'name': 'gateway', 'pid': 2, 'identity': self.identities[2]}]}
        (self.state / 'supervisor.json').write_text(json.dumps(record))
        self.ports.add(4141)
        return record

    def test_initial_observation_is_stopped_without_spawning(self):
        self.assertEqual(self.lifecycle.status()['state'], 'stopped')
        self.spawn.assert_not_called()

    def test_start_detaches_and_then_loads_selected_profile(self):
        def spawned(*args, **kwargs):
            self.seed_ready()
            return Mock(poll=Mock(return_value=None))
        self.spawn.side_effect = spawned
        result = self.lifecycle.start('inkling2-mini', 8192)
        self.assertEqual(result['status'], 'started')
        self.client.load.assert_called_once_with('inkling2-mini', 8192)
        self.assertIn('--serve', self.spawn.call_args.args[0])
        self.assertIn('--registry', self.spawn.call_args.args[0])

    def test_existing_verified_stack_attaches_without_another_spawn(self):
        self.seed_ready()
        self.assertEqual(self.lifecycle.start('saluki27b', 150016)['status'], 'attached')
        self.spawn.assert_not_called()
        self.client.load.assert_called_once_with('saluki27b', 150016)

    def test_attach_rejects_direct_route_mismatch(self):
        self.seed_ready()
        with self.assertRaises(StackError):
            self.lifecycle.start(direct=True)
        self.spawn.assert_not_called()
        self.client.load.assert_not_called()

    def test_busy_stop_refuses_without_queue_or_signal(self):
        self.seed_ready()
        self.client.status.return_value['busy'] = True
        with self.assertRaises(StackError) as error:
            self.lifecycle.stop()
        self.assertEqual(error.exception.status, 409)
        self.client.stop_stack.assert_not_called()

    def test_complete_stop_requires_observed_identities_and_ports_gone(self):
        self.seed_ready()
        def stop(expected_generation):
            self.assertEqual(expected_generation, 'generation-1')
            self.identities.clear()
            self.ports.clear()
            return {'status': 'stopping', 'generation': 'generation-1'}
        self.client.stop_stack.side_effect = stop
        self.assertEqual(self.lifecycle.stop()['status'], 'stopped')
        self.spawn.assert_not_called()

    def test_lost_ack_can_still_report_observed_complete_shutdown(self):
        self.seed_ready()
        def lost_after_stop(expected_generation):
            self.identities.clear()
            self.ports.clear()
            raise StackError('response lost after supervisor exit', 503)
        self.client.stop_stack.side_effect = lost_after_stop
        self.assertEqual(self.lifecycle.stop()['status'], 'stopped')

    def test_new_listener_after_owned_shutdown_is_not_touched(self):
        self.seed_ready()
        def stopped(expected_generation):
            self.identities.clear()
            self.ports.add(18081)
            return {'status': 'stopping', 'generation': 'generation-1'}
        self.client.stop_stack.side_effect = stopped
        result = self.lifecycle.stop()
        self.assertEqual(result['status'], 'uncertain')
        self.assertIn(18081, result['occupied_ports'])

    def test_offline_registered_profiles_and_context_choices_persist(self):
        engine = self.root / 'llama-server.exe'
        model = {'id': 'base', 'name': 'Base', 'path': str(self.root / 'base.gguf'), 'engine': str(engine), 'context': 8192, 'max_output_tokens': 1024}
        self.registry.write_text(json.dumps({'schema_version': 1, 'models': [model]}))
        extra = {**model, 'id': 'extra', 'name': 'Extra', 'path': str(self.root / 'extra.gguf')}
        (self.state / 'overrides.json').write_text(json.dumps({'models': {'base': 4096}, 'registered': [extra]}))
        observed = self.lifecycle.status()
        self.assertEqual(observed['state'], 'stopped')
        self.assertEqual([entry['id'] for entry in observed['models']], ['base', 'extra'])
        self.assertEqual(observed['models'][0]['context'], 4096)
        self.assertFalse(observed['models'][0]['context_verified'])
        self.spawn.assert_not_called()

    def test_stop_response_loss_is_uncertain_and_never_pid_kills(self):
        self.seed_ready()
        self.client.stop_stack.side_effect = StackError('lost acknowledgement', 503)
        self.assertEqual(self.lifecycle.stop()['status'], 'uncertain')
        self.assertEqual(len(self.identities), 2)

    def test_shutdown_timeout_is_uncertain_not_success(self):
        self.seed_ready()
        self.client.stop_stack.return_value = {'status': 'stopping', 'generation': 'generation-1'}
        self.assertEqual(self.lifecycle.stop()['status'], 'uncertain')
        self.assertEqual(len(self.identities), 2)

    def test_stop_post_carries_preflight_generation_to_replacement_gateway(self):
        record = self.seed_ready()
        opener = Mock()
        client = StackClient(state_dir=self.state, opener=opener)
        client.status = self.client.status
        (self.state / 'control.token').write_text('c' * 48)
        self.lifecycle.client = client

        def replacement_gateway(request, timeout):
            # Port 4141 changed hands after status verification and the last record check.
            record['generation'] = 'generation-2'
            (self.state / 'supervisor.json').write_text(json.dumps(record))
            self.assertEqual(request.get_method(), 'POST')
            self.assertTrue(request.full_url.endswith('/control/stop-stack'))
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + 'c' * 48)
            self.assertEqual(json.loads(request.data), {'expected_generation': 'generation-1'})
            self.assertEqual(timeout, 20)
            raise HTTPError(request.full_url, 409, 'Conflict', {},
                            io.BytesIO(b'{"error":{"message":"Supervisor generation changed"}}'))

        opener.open.side_effect = replacement_gateway
        with self.assertRaises(StackError) as error:
            self.lifecycle.stop()
        self.assertEqual(error.exception.status, 409)
        opener.open.assert_called_once()
        self.spawn.assert_not_called()

    def test_restart_generation_during_stop_is_never_stopped(self):
        record = self.seed_ready()
        def stop(expected_generation):
            record['generation'] = 'generation-2'
            (self.state / 'supervisor.json').write_text(json.dumps(record))
            return {'status': 'stopping', 'generation': 'generation-1'}
        self.client.stop_stack.side_effect = stop
        self.assertEqual(self.lifecycle.stop()['status'], 'uncertain')
        self.assertEqual(len(self.identities), 2)

    def test_reused_pid_and_unknown_listener_refuse_adoption(self):
        self.seed_ready()
        self.identities[1] = {'executable': 'other', 'created': 'later'}
        self.assertFalse(self.lifecycle.status()['owned'])
        with self.assertRaises(StackError):
            self.lifecycle.start()
        with self.assertRaises(StackError):
            self.lifecycle.stop()
        self.client.stop_stack.assert_not_called()
        self.spawn.assert_not_called()

    def test_stale_record_with_no_listeners_is_already_stopped(self):
        self.seed_ready()
        self.identities.clear()
        self.ports.clear()
        self.assertEqual(self.lifecycle.stop()['status'], 'already_stopped')

    def test_legacy_supervisor_is_not_accepted_by_pid_alone(self):
        record = self.seed_ready()
        record.pop('stop_protocol')
        (self.state / 'supervisor.json').write_text(json.dumps(record))
        self.assertEqual(self.lifecycle.status()['state'], 'unknown')
        self.assertFalse(self.lifecycle.status()['owned'])
        with self.assertRaises(StackError):
            self.lifecycle.stop()

    def test_busy_lifecycle_action_is_not_queued(self):
        self.lifecycle.action_lock.acquire()
        try:
            with self.assertRaises(StackError):
                self.lifecycle.start()
            with self.assertRaises(StackError):
                self.lifecycle.stop()
        finally:
            self.lifecycle.action_lock.release()

    def test_start_timeout_leaves_owned_launch_for_observation(self):
        child = Mock()
        child.poll.return_value = None
        self.spawn.return_value = child
        result = self.lifecycle.start()
        self.assertEqual(result['status'], 'uncertain')
        child.terminate.assert_not_called()
        child.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
