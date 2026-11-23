"""HTTP tests use ephemeral loopback servers and fake in-memory inference."""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from local_stack.client import StackClient
from local_stack.gateway import GatewayServer, model_catalog
from local_stack.registry import StackError
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

    def test_catalog_requires_inference_key_and_exposes_normalized_capacity(self):
        status, payload = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"][0]["context_window"], 8192)
        self.assertFalse(payload["data"][0]["context_verified"])
        self.assertNotIn("engine", payload["data"][0])
        self.assertEqual(self.request("GET", "/v1/models", headers={"Authorization": "wrong"})[0], 401)

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
        self.assertEqual(self.request("POST", "/v1/responses", {"model": "saluki27b", "input": "hello"})[0], 501)

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
