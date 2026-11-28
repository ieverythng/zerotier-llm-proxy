"""HTTP tests use ephemeral loopback servers and fake in-memory inference."""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from email.message import Message
from types import SimpleNamespace
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from local_stack.client import StackClient
from local_stack.gateway import GatewayHandler, GatewayServer, model_catalog, prepare_chat
from local_stack.request_flow import RequestFlow
from local_stack.registry import StackError, load_registry
from local_stack.runtime import ModelRuntime


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, payload=b'{"choices":[]}'):
        self.buffer = io.BytesIO(payload)
    def read(self, count=-1):
        return self.buffer.read(count)
    read1 = read
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.buffer.close()


class InklingOutputPolicyTests(unittest.TestCase):
    def test_only_long_inkling_gets_the_larger_budget(self):
        registry = load_registry(Path(__file__).resolve().parents[1] / "config" / "models.json")
        models = {row["id"]: row for row in registry["models"]}
        long = models["inkling2-mini-long"]
        short = models["inkling2-mini"]
        self.assertEqual((long["minimum_context"], long["context"], long["maximum_context"]), (150016,) * 3)
        self.assertEqual(long['experimental_resources'],{'minimum_free_vram_mib':512,'minimum_available_ram_gib':1.25})
        fallback=models['inkling2-mini-100k']
        self.assertEqual((fallback['minimum_context'],fallback['context'],fallback['maximum_context']),(100096,)*3)
        self.assertEqual(fallback['input'],['text','image'])
        self.assertNotIn('experimental_resources',fallback)
        self.assertNotIn('experimental_resources',models['saluki27b'])
        self.assertEqual(registry['resources']['minimum_free_vram_mib'],2048)
        self.assertEqual(short["max_output_tokens"], 2048)
        self.assertEqual(models["saluki27b"]["max_output_tokens"], 4096)
        self.assertEqual(long["max_output_tokens"], 10000)
        base = {"model": long["id"], "messages": []}
        self.assertEqual(prepare_chat(base, long)["max_tokens"], 4096)
        for field in ("max_tokens", "max_completion_tokens"):
            with self.subTest(field=field):
                self.assertEqual(prepare_chat({**base, field: 4096}, long)[field], 4096)
                self.assertEqual(prepare_chat({**base, field: 10000}, long)[field], 10000)
                with self.assertRaisesRegex(StackError, "between 1 and 10000"):
                    prepare_chat({**base, field: 10001}, long)
                with self.assertRaisesRegex(StackError, "between 1 and 2048"):
                    prepare_chat({**base, "model": short["id"], field: 4096}, short)


class GatewayTerminalAdmissionTests(unittest.TestCase):
    """Call the real handler synchronously so the close race needs no sleeps."""

    finish = b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"length"}]}\r\n\r\n'
    usage = b'data: {"choices":[],"usage":{"completion_tokens":4096}}\r\n\r\n'
    done = b'data: [DONE]\r\n\r\n'

    def setUp(self):
        self.lock = lock = threading.Lock()
        self.runtime = runtime = SimpleNamespace(lock=lock, operation=None, admission_state='open',
                                  find_model=lambda alias: {'id':alias, 'input':['text'], 'max_output_tokens':10000},
                                  ensure_loaded=lambda alias: None, _backend_idle=Mock())
        runtime.acquire = lambda operation: ModelRuntime.acquire(runtime, operation)
        runtime.release = lambda: ModelRuntime.release(runtime)
        self.dispatches, self.closed, self.timeouts = [], [], []
        self.parts = [self.finish, self.usage, self.done[:10], self.done[10:]]
        self.server = self.make_server(runtime)

    def make_server(self, runtime):
        testcase = self

        class Upstream:
            status = 200
            headers = {'Content-Type': 'text/event-stream'}

            def __init__(self, parts, first):
                self.parts = iter(parts)
                self.first = first
                self.ended = False
                self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=testcase.timeouts.append)))

            def read(self, count):
                testcase.assertTrue(testcase.lock.locked())
                chunk = next(self.parts, b'')
                if isinstance(chunk, Exception):
                    raise chunk
                self.ended = not chunk
                return chunk

            read1 = read

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                if self.first:
                    testcase.closed.append(self.ended)

        def open_upstream(request, timeout):
            self.dispatches.append(timeout)
            first = len(self.dispatches) == 1
            return Upstream(self.parts if first else [self.finish, self.done], first)

        return SimpleNamespace(runtime=runtime, request_flow=RequestFlow(), upstream='http://127.0.0.1:18081/v1',
                               upstream_headers=lambda:{}, opener=SimpleNamespace(open=open_upstream))

    def request(self, on_flush=lambda data: None):
        h = object.__new__(GatewayHandler)
        h.path = '/v1/chat/completions'
        h.server = self.server
        h._validate_request = lambda **kwargs: None
        h._body = lambda: {'model':'test', 'messages':[], 'stream':True}
        h.send_response = lambda status: setattr(h,'code',status)
        h.send_header = lambda *args: None
        h.end_headers = lambda: None
        h._error = lambda error: setattr(h,'code',error.status)

        class Sink(io.BytesIO):
            def flush(self):
                on_flush(self.getvalue())

        h.wfile = Sink()
        h.do_POST()
        return h.code, h.wfile.getvalue()

    def test_followup_at_complete_done_sentinel_is_admitted(self):
        observed, verified_idle = [], []
        self.runtime._backend_idle.side_effect = lambda: verified_idle.append((self.closed == [True], self.lock.locked()))

        def at_done(payload):
            if self.done in payload and not observed:
                closed_before_done = self.closed == [True]
                locked_before_done = self.lock.locked()
                observed.append((closed_before_done, locked_before_done, self.request()[0]))

        self.assertEqual(self.request(at_done), (200, self.finish + self.usage + self.done))
        self.assertEqual(observed, [(True, False, 200)])
        self.assertEqual(verified_idle, [(True, True), (True, True)])
        self.assertEqual(self.dispatches, [1200, 1200])
        self.assertEqual(len(self.timeouts), 2)
        self.assertTrue(all(0 < timeout <= 1 for timeout in self.timeouts))
        self.assertFalse(self.lock.locked())

    def test_drain_timeout_withholds_done_and_sanitizes_failure(self):
        self.parts.append(TimeoutError('PRIVATE_UPSTREAM'))
        _, wire = self.request()
        self.assertNotIn(self.done, wire)
        self.assertNotIn(b'PRIVATE_UPSTREAM', wire)
        self.assertIn(b'stream_completion_unverified', wire)
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.closed, [False])
        self.runtime._backend_idle.assert_not_called()
        self.assertEqual(self.server.request_flow.snapshot()['last']['outcome'], 'failed')

    def test_occupied_backend_after_upstream_eof_does_not_signal_done(self):
        def verify():
            self.assertTrue(self.lock.locked())
            self.assertEqual(self.closed, [True])
            raise StackError('BACKEND_BUSY', 409)

        self.runtime._backend_idle.side_effect = verify
        _, wire = self.request()
        self.assertNotIn(self.done, wire)
        self.assertNotIn(b'BACKEND_BUSY', wire)
        self.assertIn(b'stream_completion_unverified', wire)
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.server.request_flow.snapshot()['last']['outcome'], 'failed')


class GatewayStopGenerationTests(unittest.TestCase):
    """Exercise the authenticated handler without opening a listener."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.runtime = SimpleNamespace(state_dir=self.state, lock=threading.Lock(),
                                       operation=None, admission_state='open', _stop_current=Mock())
        self.runtime.acquire = lambda operation: ModelRuntime.acquire(self.runtime, operation)
        self.runtime.release = lambda: ModelRuntime.release(self.runtime)
        self.runtime.shutdown_stack = lambda submit: ModelRuntime.shutdown_stack(self.runtime, submit)
        self.server = object.__new__(GatewayServer)
        self.server.runtime = self.runtime
        self.server.server_port = 4141
        self.server.request_flow = RequestFlow()
        self.server.control_key = 'c' * 48
        gateway = {'name': 'gateway', 'pid': os.getpid(), 'identity': {'created': 'gateway'}}
        owner = {'pid': gateway['pid'] + 1, 'identity': {'created': 'supervisor'}, 'generation': 'generation-2',
                 'status': 'ready', 'stop_protocol': 1, 'children': [gateway]}
        (self.state / 'supervisor.json').write_text(json.dumps(owner), encoding='utf-8')
        self.enterContext(patch.dict(os.environ, {'LOCAL_STACK_GENERATION': 'generation-2', 'LOCAL_STACK_STOP_KEY': 'k' * 48}))
        identities = {entry['pid']: entry['identity'] for entry in (owner, gateway)}
        self.enterContext(patch('local_stack.gateway.process_identity', side_effect=identities.get))

    def request(self, body, *, authorized=True):
        payload = json.dumps(body).encode('utf-8')
        handler = object.__new__(GatewayHandler)
        handler.path = '/control/stop-stack'
        handler.server = self.server
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:4141'
        handler.headers['Authorization'] = 'Bearer ' + (self.server.control_key if authorized else 'i' * 48)
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(payload))
        handler.connection = Mock()
        handler.rfile = io.BytesIO(payload)
        handler._json = Mock()
        handler.do_POST()
        return handler._json.call_args.args

    def test_replacement_gateway_refuses_stale_stop_without_effects(self):
        status, _ = self.request({'expected_generation': 'generation-1'})
        self.assertEqual(status, 409)
        self.runtime._stop_current.assert_not_called()
        self.assertEqual(self.runtime.admission_state, 'open')
        self.assertFalse(self.runtime.lock.locked())
        self.assertFalse((self.state / 'stop-intent.json').exists())

    def test_missing_or_malformed_generation_cannot_stop_stack(self):
        for body in ({}, {'expected_generation': ''}, {'expected_generation': None},
                     {'expected_generation': 2}, {'expected_generation': ['generation-2']}):
            with self.subTest(body=body):
                self.assertEqual(self.request(body)[0], 400)
        self.runtime._stop_current.assert_not_called()
        self.assertEqual(self.runtime.admission_state, 'open')
        self.assertFalse((self.state / 'stop-intent.json').exists())

    def test_matching_generation_still_requires_control_token(self):
        self.assertEqual(self.request({'expected_generation': 'generation-2'}, authorized=False)[0], 401)
        self.runtime._stop_current.assert_not_called()
        self.assertEqual(self.runtime.admission_state, 'open')
        self.assertFalse((self.state / 'stop-intent.json').exists())

    def test_matching_generation_remains_nonblocking_when_busy(self):
        self.runtime.acquire('inference')
        try:
            self.assertEqual(self.request({'expected_generation': 'generation-2'})[0], 409)
            self.assertEqual(self.runtime.operation, 'inference')
            self.runtime._stop_current.assert_not_called()
            self.assertEqual(self.runtime.admission_state, 'open')
            self.assertFalse((self.state / 'stop-intent.json').exists())
        finally:
            self.runtime.release()

    def test_matching_generation_still_seals_admission_and_submits_intent(self):
        status, body = self.request({'expected_generation': 'generation-2'})
        self.assertEqual(status, 200)
        self.assertEqual(body, {'status': 'stopping', 'generation': 'generation-2'})
        self.runtime._stop_current.assert_called_once_with()
        self.assertEqual(self.runtime.admission_state, 'stopping')
        self.assertFalse(self.runtime.lock.locked())
        intent = json.loads((self.state / 'stop-intent.json').read_text(encoding='utf-8'))
        self.assertEqual(intent['payload']['generation'], 'generation-2')
        self.assertEqual(intent['payload']['gateway_identity'], {'created': 'gateway'})


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / "models.json"
        self.registry.write_text(json.dumps({"schema_version": 1, "models": [{"id": "saluki27b", "name": "Saluki", "path": str(self.root / "model.gguf"), "engine": str(self.root / "llama-server.exe"), "context": 8192, "max_output_tokens": 2048}]}))
        self.runtime = ModelRuntime(self.registry, self.root / "state", resources=lambda: {"available_ram_gib": 8, "free_vram_mib": 16000, "gpu_utilization_percent": 0}, identify=lambda pid: None)
        self.runtime.ensure_loaded = Mock()
        self.inference_key = "i" * 48
        self.control_key = "c" * 48
        self.opener = Mock()
        self.opener.open.return_value = FakeResponse()
        self.server = GatewayServer(("127.0.0.1", 0), self.runtime, inference_key=self.inference_key, control_key=self.control_key, upstream="http://127.0.0.1:18081/v1", opener=self.opener)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.environment = patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "b" * 48})
        self.environment.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.environment.stop()
        self.temporary.cleanup()

    def request(self, method, path, body=None, *, control=False, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        actual = {"Authorization": "Bearer " + (self.control_key if control else self.inference_key)}
        if headers:
            actual.update(headers)
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            actual["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=actual)
        response = connection.getresponse()
        status = response.status
        result = json.loads(response.read())
        connection.close()
        return status, result

    def test_request_flow_is_authenticated_and_status_polls_do_not_increment(self):
        self.assertEqual(self.request('GET', '/control/status')[0], 401)
        first = self.request('GET', '/control/status', control=True)[1]['request_flow']
        second = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(first['counters'], second['counters'])
        self.assertEqual(first['counters']['ingress'], 0)
        self.assertEqual(first['route'], 'direct')
        self.assertEqual(first['backend']['state'], 'unknown')

    def test_chat_uses_twenty_minute_upstream_socket_timeout(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.opener.open.return_value = FakeResponse()
                status, _ = self.request('POST', '/v1/chat/completions', {'model': 'saluki27b', 'messages': [], 'stream': stream})
                self.assertEqual(status, 200)
                self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 1200)

    def test_request_flow_tracks_success_without_prompt_output_or_credentials(self):
        self.opener.open.return_value = FakeResponse(b'{"choices":[{"message":{"content":"PRIVATE_OUTPUT"}}]}')
        status, _ = self.request('POST', '/v1/chat/completions', {'model': 'saluki27b', 'messages': [{'role': 'user', 'content': 'PRIVATE_PROMPT'}]})
        self.assertEqual(status, 200)
        flow = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(flow['counters']['completed'], 1)
        self.assertEqual(flow['counters']['accepted'], 1)
        self.assertEqual(flow['counters']['active'], 0)
        self.assertIsNotNone(flow['last']['first_content_ms'])
        self.assertGreater(flow['last']['bytes_forwarded'], 0)
        serialized = json.dumps(flow)
        for private in ('PRIVATE_OUTPUT', 'PRIVATE_PROMPT', self.inference_key, self.control_key):
            self.assertNotIn(private, serialized)

    def test_rejected_busy_and_unauthorized_requests_are_not_accepted(self):
        self.runtime.acquire('inference')
        try:
            self.assertEqual(self.request('POST', '/v1/chat/completions', {'model': 'saluki27b', 'messages': []})[0], 409)
        finally:
            self.runtime.release()
        self.assertEqual(self.request('POST', '/v1/chat/completions', {'model': 'saluki27b', 'messages': []}, headers={'Authorization': 'invalid'})[0], 401)
        flow = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(flow['counters']['ingress'], 2)
        self.assertEqual(flow['counters']['accepted'], 0)
        self.assertEqual(flow['counters']['rejected'], 2)
        self.assertEqual(flow['last']['error_kind'], 'authorization')

    def test_upstream_reset_is_failure_not_local_cancellation(self):
        class ResetResponse(FakeResponse):
            def read1(self, count):
                raise ConnectionResetError('PRIVATE_UPSTREAM_ERROR')
        self.opener.open.return_value = ResetResponse()
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        connection.request('POST', '/v1/chat/completions', body=json.dumps({'model': 'saluki27b', 'messages': []}), headers={'Authorization': 'Bearer ' + self.inference_key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        connection.close()
        flow = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(flow['counters']['failed'], 1)
        self.assertEqual(flow['counters']['cancelled'], 0)
        self.assertEqual(flow['last']['error_kind'], 'upstream_io')
        self.assertNotIn('PRIVATE_UPSTREAM_ERROR', json.dumps(flow))

    def test_observed_client_write_disconnect_is_local_cancel(self):
        from local_stack.gateway import GatewayHandler
        with patch('local_stack.benchmark.run_benchmark', return_value={'status': 'ok'}), patch.object(GatewayHandler, '_json', side_effect=BrokenPipeError('private-local-write')):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
            connection.request('POST', '/control/benchmark', body=json.dumps({'model': 'saluki27b'}), headers={'Authorization': 'Bearer ' + self.control_key, 'Content-Type': 'application/json'})
            with self.assertRaises(http.client.RemoteDisconnected):
                connection.getresponse()
            connection.close()
        flow = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(flow['counters']['cancelled'], 1)
        self.assertEqual(flow['last']['error_kind'], 'local_disconnect')
        self.assertFalse(self.runtime.lock.locked())

    def test_full_stop_writes_generation_bound_intent_and_seals_admission(self):
        owner = {'pid': 123, 'identity': {'created': 'supervisor'}, 'generation': 'g1'}
        gateway = {'name': 'gateway', 'pid': 456, 'identity': {'created': 'gateway'}}
        self.runtime._stop_current = Mock()
        with patch.object(self.server, '_stop_owner', return_value=(owner, gateway, 'k' * 48)):
            status, body = self.request('POST', '/control/stop-stack', {'expected_generation': 'g1'}, control=True)
        self.assertEqual(status, 200)
        self.assertEqual(body, {'status': 'stopping', 'generation': 'g1'})
        intent = json.loads((self.runtime.state_dir / 'stop-intent.json').read_text())
        self.assertEqual(intent['payload']['gateway_identity'], gateway['identity'])
        self.assertEqual(intent['payload']['generation'], 'g1')
        self.assertEqual(self.runtime.admission_state, 'stopping')
        self.assertEqual(self.request('POST', '/control/load', {'model': 'saluki27b'}, control=True)[0], 503)

    def test_vision_quick_benchmark_is_control_only_and_keeps_admission(self):
        def benchmark(runtime, model, context=None, profile='quick'):
            self.assertTrue(runtime.lock.locked())
            self.assertEqual((model, profile), ('saluki27b', 'vision-quick'))
            return {'status': 'ok', 'quality': {'passed': True}}
        with patch('local_stack.benchmark.run_benchmark', side_effect=benchmark) as run:
            self.assertEqual(self.request('POST', '/control/benchmark', {'model':'saluki27b','profile':'vision-quick'})[0],401)
            self.assertEqual(self.request('POST', '/control/benchmark', {'model':'saluki27b','profile':'vision-quick','prompt':'untrusted'},control=True)[0],400)
            for invalid in ([],{},None,1):
                self.assertEqual(self.request('POST','/control/benchmark',{'model':'saluki27b','profile':invalid},control=True)[0],400)
            self.assertEqual(self.request('POST', '/control/benchmark', {'model':'saluki27b','profile':'vision-quick'},control=True)[0],200)
            self.assertEqual(run.call_count,1)
        self.assertFalse(self.runtime.lock.locked())

    def test_benchmark_holds_admission_across_all_internal_requests(self):
        def benchmark(runtime, model, context=None, profile='quick'):
            self.assertTrue(runtime.lock.locked())
            self.assertEqual(runtime.operation, 'benchmark')
            with self.assertRaises(StackError):
                runtime.stop()
            return {'status': 'ok', 'model': model, 'profile': profile}
        with patch('local_stack.benchmark.run_benchmark', side_effect=benchmark):
            status, body = self.request('POST', '/control/benchmark', {'model': 'saluki27b'}, control=True)
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertFalse(self.runtime.lock.locked())

    def test_benchmark_overlap_refused_without_running_worker(self):
        self.runtime.acquire('inference')
        try:
            with patch('local_stack.benchmark.run_benchmark') as worker:
                self.assertEqual(self.request('POST', '/control/benchmark', {'model': 'saluki27b'}, control=True)[0], 409)
                worker.assert_not_called()
        finally:
            self.runtime.release()

    def test_benchmark_unknown_profile_refused(self):
        self.assertEqual(self.request('POST', '/control/benchmark', {'model': 'saluki27b', 'profile': 'unbounded'}, control=True)[0], 400)

    def test_standalone_gateway_never_full_stops_by_pid_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.request('POST', '/control/stop-stack', {'expected_generation': 'g1'}, control=True)[0], 501)
        self.assertEqual(self.runtime.admission_state, 'open')

    def test_control_stop_stack_has_no_inference_token_authority(self):
        self.assertEqual(self.request('POST', '/control/stop-stack', {'expected_generation': 'g1'})[0], 401)

    def test_catalog_requires_inference_key_and_exposes_normalized_capacity(self):
        status, payload = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"][0]["context_window"], 8192)
        self.assertFalse(payload["data"][0]["context_verified"])
        self.assertNotIn("engine", payload["data"][0])
        self.assertEqual(self.request("GET", "/v1/models", headers={"Authorization": "wrong"})[0], 401)

    def test_catalog_exposes_only_numeric_experimental_policy_metadata(self):
        model={**self.runtime.all_models()[0], 'experimental_resources':{'minimum_free_vram_mib':512,'minimum_available_ram_gib':1.25,'private_note':'PRIVATE'}}
        with patch.object(self.runtime,'all_models',return_value=[model]):
            row=model_catalog(self.runtime)['data'][0]
        self.assertEqual(row['experimental_resources'],{'minimum_free_vram_mib':512,'minimum_available_ram_gib':1.25})
        self.assertNotIn('PRIVATE',json.dumps(row))
        self.assertNotIn('experimental_resources',model_catalog(self.runtime)['data'][0])

    def test_control_token_is_not_an_inference_token(self):
        self.assertEqual(self.request("GET", "/control/status")[0], 401)
        self.assertEqual(self.request("GET", "/control/status", control=True)[0], 200)
        self.assertEqual(self.request("GET", "/v1/models", control=True)[0], 401)

    def test_foreign_origin_and_rebinding_host_rejected(self):
        for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Origin": "null"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/health", headers=headers)[0], 403)

    def test_health_reveals_no_credentials_or_files(self):
        status, payload = self.request("GET", "/health", headers={"Authorization": "wrong"})
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"status", "gateway", "model_ready"})

    def test_chat_forwards_only_backend_credentials_and_bounds_output(self):
        status, _ = self.request("POST", "/v1/chat/completions", {"model": "saluki27b", "messages": []})
        self.assertEqual(status, 200)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "b" * 48)
        self.assertEqual(json.loads(request.data)["max_tokens"], 2048)
        self.assertFalse(self.runtime.lock.locked())
        self.runtime.ensure_loaded.assert_called_once_with("saluki27b")

    def test_accepts_dsh_no_store_with_completion_budget(self):
        body = {"model": "saluki27b", "messages": [], "store": False, "max_completion_tokens": 256,
                "stream": True, "stream_options": {"include_usage": True}}
        self.assertEqual(self.request("POST", "/v1/chat/completions", body)[0], 200)
        outbound = json.loads(self.opener.open.call_args.args[0].data)
        self.assertNotIn("store", outbound)
        self.assertEqual(outbound["max_completion_tokens"], 256)
        self.assertEqual(outbound["stream_options"], {"include_usage": True})

    def test_refuses_storage_requests(self):
        for invalid in (True, None, "false", 0):
            with self.subTest(invalid=invalid):
                body = {"model": "saluki27b", "messages": [], "store": invalid}
                self.assertEqual(self.request("POST", "/v1/chat/completions", body)[0], 400)
        self.runtime.ensure_loaded.assert_not_called()
        self.opener.open.assert_not_called()

    def test_busy_request_rejected_without_touching_model(self):
        self.runtime.acquire("inference")
        try:
            status, _ = self.request("POST", "/v1/chat/completions", {"model": "saluki27b", "messages": []})
            self.assertEqual(status, 409)
            self.runtime.ensure_loaded.assert_not_called()
        finally:
            self.runtime.release()

    def test_upstream_error_body_never_leaks(self):
        self.opener.open.side_effect = urllib.error.HTTPError("http://127.0.0.1/private", 500, "error", {}, io.BytesIO(b"secret-prompt-key"))
        status, payload = self.request("POST", "/v1/chat/completions", {"model": "saluki27b", "messages": []})
        self.assertEqual(status, 502)
        self.assertNotIn("secret-prompt-key", json.dumps(payload))
        self.assertFalse(self.runtime.lock.locked())

    def test_unvalidated_responses_transport_refused(self):
        request = {"model": "saluki27b", "input": "synthetic sentinel", "stream": True, "store": False}
        self.assertEqual(self.request("POST", "/v1/responses", request, control=True)[0], 401)
        self.runtime.acquire("inference")
        try:
            status, response = self.request("POST", "/v1/responses", request)
            self.assertEqual(status, 501)
            self.assertNotIn("synthetic sentinel", json.dumps(response))
            self.assertTrue(self.runtime.lock.locked(), "disabled Responses must not release another request's lease")
            self.runtime.ensure_loaded.assert_not_called()
            self.opener.open.assert_not_called()
        finally:
            self.runtime.release()

    def test_control_configure_updates_catalog_and_uses_aligned_context(self):
        status, result = self.request("POST", "/control/configure", {"model": "saluki27b", "context": 5000}, control=True)
        self.assertEqual(status, 200)
        self.assertEqual(result["context"], 5120)
        self.assertEqual(self.request("GET", "/v1/models")[1]["data"][0]["context_window"], 5120)

    def test_proxy_does_not_forward_client_auth_or_control_token(self):
        self.server.direct = False
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "proxy-key", "HEADROOM_PROXY_TOKEN": "memory-key", "HEADROOM_PROJECT_ID": "local-tests", "HEADROOM_USER_ID": "operator"}):
            headers = self.server.upstream_headers()
        self.assertEqual(headers["Authorization"], "Bearer proxy-key")
        self.assertEqual(headers["x-headroom-proxy-token"], "memory-key")
        self.assertNotIn(self.inference_key, repr(headers))
        self.assertNotIn(self.control_key, repr(headers))

    def test_admission_is_held_for_complete_sse_lifetime(self):
        started = threading.Event()
        complete = threading.Event()
        class StreamResponse(FakeResponse):
            headers = {"Content-Type": "text/event-stream"}
            def __init__(self):
                self.calls = 0
            def read1(self, count):
                self.calls += 1
                if self.calls == 1:
                    return b'data: {"choices":[]}\n\n'
                started.set()
                complete.wait(4)
                return b""
            def __exit__(self, *args):
                pass
        self.opener.open.return_value = StreamResponse()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.request("POST", "/v1/chat/completions", body=json.dumps({"model": "saluki27b", "messages": [], "stream": True}), headers={"Authorization": "Bearer " + self.inference_key, "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertTrue(started.wait(2))
        self.assertTrue(self.runtime.lock.locked())
        flow = self.request('GET', '/control/status', control=True)[1]['request_flow']
        self.assertEqual(flow['counters']['active'], 1)
        self.assertEqual(flow['active'][0]['kind'], 'inference')
        self.assertTrue(flow['active'][0]['streaming'])
        self.assertEqual(flow['active'][0]['stage'], 'streaming')
        self.assertIsNone(flow['active'][0]['first_content_ms'])
        self.assertGreater(flow['active'][0]['bytes_forwarded'], 0)
        self.assertEqual(self.request("POST", "/control/stop", {}, control=True)[0], 409)
        complete.set()
        self.assertIn(b"data:", response.read())
        connection.close()

    def test_client_discovers_private_local_control_credential(self):
        (self.root / "state" / "control.token").write_text(self.control_key)
        (self.root / "state" / "inference.token").write_text(self.inference_key)
        client = StackClient(f"http://127.0.0.1:{self.port}", state_dir=self.root / "state")
        self.assertFalse(client.status()["busy"])
        self.assertEqual(client.models()[0]["id"], "saluki27b")

    def test_rejects_nonloopback_gateway_and_upstream(self):
        for address, upstream in ((("0.0.0.0", 0), "http://127.0.0.1:18081/v1"), (("127.0.0.1", 0), "http://example.com/v1")):
            with self.assertRaises(StackError):
                GatewayServer(address, self.runtime, inference_key=self.inference_key, control_key=self.control_key, upstream=upstream)


if __name__ == "__main__":
    unittest.main()
