"""Unit tests: no DSH profile, services, network, GPU, or real credentials."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/dsh/register_local_models.py"
SPEC = importlib.util.spec_from_file_location("dsh_local_registration", SCRIPT)
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


def registry():
    return {"schema_version": 1, "models": [
        {"id": "saluki27b", "name": "Saluki 27B", "context": 150016,
         "max_output_tokens": 4096, "input": ["text", "image"]},
        {"id": "inkling2-mini", "name": "Inkling 2 Mini", "context": 8192,
         "max_output_tokens": 2048, "input": ["text"]},
    ]}


class FakeRpc:
    def __init__(self, existing=None):
        self.descriptor = {"ns": "llm-pi-ai", "revision": 7, "value": {
            "providers": {"openai-codex": {"models": [{"id": "hosted"}], "custom": "keep"},
                          **({"watson-local": existing} if existing is not None else {})}}}
        self.calls = []
        self.credential = None
        self.fail_credential = False
        self.fail_mutation = False

    def call(self, endpoint, args):
        self.calls.append((endpoint, copy.deepcopy(args)))
        if endpoint == "settings/describe":
            return {"writable": True, "namespaces": [copy.deepcopy(self.descriptor)]}
        if endpoint == "settings/mutate":
            if self.fail_mutation:
                raise registration.RegistrationError("settings/conflict")
            assert args["expectedRevision"] == self.descriptor["revision"]
            for op in args["ops"]:
                assert op["path"][:2] == ["providers", "watson-local"]
                target = self.descriptor["value"]["providers"].setdefault("watson-local", {})
                target[op["path"][2]] = copy.deepcopy(op["value"])
            self.descriptor["revision"] += 1
            return copy.deepcopy(self.descriptor)
        if endpoint == "credentials/set":
            if self.fail_credential:
                raise registration.RegistrationError("do not expose details")
            self.credential = args["value"]
            return None
        if endpoint == "credentials/describe":
            return {"LOCAL_STACK_API_KEY": {"configured": self.credential is not None}}
        raise AssertionError(endpoint)


class RegistrationTests(unittest.TestCase):
    def desired(self):
        return registration.build_provider(registry(), "http://127.0.0.1:4141/v1")

    def test_provisional_explicit_capacities(self):
        desired = self.desired()
        self.assertEqual(desired["api"], "openai-completions")
        self.assertEqual(desired["apiKeyEnv"], "LOCAL_STACK_API_KEY")
        self.assertEqual([m["contextWindow"] for m in desired["models"]], [150016, 8192])
        self.assertTrue(all("provisional" in m["name"] for m in desired["models"]))
        self.assertEqual(desired["models"][0]["input"], ["text"])
        self.assertTrue(all(m["reasoningEfforts"] is False for m in desired["models"]))

    def test_existing_projector_allows_saluki_image_only(self):
        with tempfile.TemporaryDirectory() as directory:
            projector = Path(directory) / "projector.gguf"
            projector.write_bytes(b"fixture")
            data = registry()
            for model in data["models"]:
                model["mmproj"] = str(projector)
                model["input"] = ["text", "image"]
            desired = registration.build_provider(data, "http://127.0.0.1:4141/v1")
            self.assertEqual(desired["models"][0]["input"], ["text", "image"])
            self.assertEqual(desired["models"][1]["input"], ["text"])

    def test_live_normalized_context_is_used(self):
        desired = registration.build_provider(registry(), "http://127.0.0.1:4141/v1", {"data": [
            {"id": "saluki27b", "context_window": 50176, "max_output_tokens": 4096,
             "context_verified": True}]})
        self.assertEqual(desired["models"][0]["contextWindow"], 50176)
        self.assertIn("verified", desired["models"][0]["name"])
        self.assertNotIn("provisional", desired["models"][0]["name"])
        self.assertIn("provisional", desired["models"][1]["name"])

    def test_listing_without_attestation_is_provisional(self):
        desired = registration.build_provider(registry(), "http://127.0.0.1:4141/v1", {
            "data": [{"id": "saluki27b", "context_window": 50176}]})
        self.assertIn("provisional", desired["models"][0]["name"])

    def test_bool_context_rejected(self):
        data = registry()
        data["models"][0]["context"] = True
        with self.assertRaises(registration.RegistrationError):
            registration.build_provider(data, "http://127.0.0.1:4141/v1")

    def test_missing_context_never_uses_dsh_fallback(self):
        data = registry()
        del data["models"][0]["context"]
        with self.assertRaises(registration.RegistrationError):
            registration.build_provider(data, "http://127.0.0.1:4141/v1")

    def test_output_must_leave_input_space(self):
        data = registry()
        data["models"][1]["max_output_tokens"] = 8192
        with self.assertRaises(registration.RegistrationError):
            registration.build_provider(data, "http://127.0.0.1:4141/v1")

    def test_missing_or_duplicate_model_rejected(self):
        for data in ({"schema": 1, "models": []},
                     {"schema": 1, "models": registry()["models"] * 2}):
            with self.assertRaises(registration.RegistrationError):
                registration.build_provider(data, "http://127.0.0.1:4141/v1")

    def test_remote_or_secret_bearing_url_rejected(self):
        for url in ("http://example.com/v1", "http://127.0.0.1.example.com/v1",
                    "http://user:secret@127.0.0.1/v1", "https://127.0.0.1/v1",
                    "http://127.0.0.1/v1?token=secret", "http://127.0.0.1/v1#secret"):
            with self.subTest(url=url), self.assertRaises(registration.RegistrationError):
                registration.build_provider(registry(), url)

    def test_only_local_leaf_operations_preserve_unknown_fields(self):
        desired = self.desired()
        existing = {**desired, "headers": {"custom": "preserve"}, "timeoutMs": 12345}
        namespace = {"value": {"providers": {"watson-local": existing, "hosted": {"keep": True}}}}
        self.assertEqual(registration.provider_ops(namespace, desired), [])
        changed = copy.deepcopy(desired)
        changed["models"][1]["contextWindow"] = 16384
        ops = registration.provider_ops(namespace, changed)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], ["providers", "watson-local", "models"])
        self.assertEqual(existing["timeoutMs"], 12345)

    def test_apply_preserves_hosted_routes_and_is_idempotent(self):
        rpc = FakeRpc()
        hosted_before = copy.deepcopy(rpc.descriptor["value"]["providers"]["openai-codex"])
        desired = self.desired()
        first = registration.apply_registration(rpc, desired, "test-not-a-secret")
        second = registration.apply_registration(rpc, desired, "test-not-a-secret")
        self.assertTrue(first["settings_changed"])
        self.assertFalse(second["settings_changed"])
        self.assertFalse(second["default_changed"])
        self.assertEqual(hosted_before, rpc.descriptor["value"]["providers"]["openai-codex"])
        self.assertEqual(sum(endpoint == "settings/mutate" for endpoint, _ in rpc.calls), 1)
        self.assertFalse(any("select" in endpoint for endpoint, _ in rpc.calls))

    def test_revision_conflict_does_not_store_credential(self):
        rpc = FakeRpc()
        rpc.fail_mutation = True
        with self.assertRaises(registration.RegistrationError):
            registration.apply_registration(rpc, self.desired(), "test-not-a-secret")
        self.assertIsNone(rpc.credential)

    def test_partial_credential_failure_is_explicit(self):
        rpc = FakeRpc()
        rpc.fail_credential = True
        with self.assertRaisesRegex(registration.RegistrationError, "settings may be saved"):
            registration.apply_registration(rpc, self.desired(), "test-not-a-secret")

    def test_snapshot_excludes_foreign_fields_and_credentials(self):
        existing = {**self.desired(), "headers": {"Authorization": "secret"}, "private": "secret"}
        rpc = FakeRpc(existing)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "public.json"
            registration.apply_registration(rpc, self.desired(), "test-not-a-secret", snapshot)
            content = snapshot.read_text()
            self.assertNotIn("secret", content)
            self.assertNotIn("headers", content)
            self.assertNotIn("hosted", content)

    def test_rpc_envelope_and_cookie_are_not_logged(self):
        class Opener:
            def open(inner, request, timeout):
                sent = json.loads(request.data)
                self.assertEqual(request.full_url, "http://127.0.0.1:64938/api/settings/describe")
                self.assertEqual(sent["method"], "settings/describe")
                self.assertEqual(sent["payload"], {"args": {}})
                self.assertEqual(request.headers["Cookie"], "session=test-only")
                return io.BytesIO(json.dumps({"type": "server-response", "rpcId": sent["rpcId"],
                                             "result": {"ok": True, "value": {"namespaces": []}}}).encode())
        rpc = registration.DshRpc("http://127.0.0.1:64938", cookie="session=test-only", opener=Opener())
        self.assertEqual(rpc.call("settings/describe", {}), {"namespaces": []})

    def test_remote_error_message_and_secret_are_not_echoed(self):
        class Opener:
            def open(inner, request, timeout):
                sent = json.loads(request.data)
                return io.BytesIO(json.dumps({"type": "server-response", "rpcId": sent["rpcId"],
                    "result": {"ok": False, "error": {"code": "settings/conflict", "message": "secret"}}}).encode())
        rpc = registration.DshRpc("http://127.0.0.1:64938", opener=Opener())
        with self.assertRaises(registration.RegistrationError) as captured:
            rpc.call("settings/describe", {})
        self.assertIn("settings/conflict", str(captured.exception))
        self.assertNotIn("secret", str(captured.exception))

    def test_launch_exchange_rejects_other_authority_without_request(self):
        rpc = registration.DshRpc("http://127.0.0.1:64938")
        with patch.object(rpc.opener, "open") as opened:
            with self.assertRaises(registration.RegistrationError):
                rpc.exchange_launch_url("http://127.0.0.1:64939/?token=secret")
            opened.assert_not_called()

    def test_redirect_is_never_followed(self):
        with self.assertRaisesRegex(registration.RegistrationError, "refusing credential"):
            registration.NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil.invalid")

    def test_dry_run_does_not_read_token_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "models.json"
            source.write_text(json.dumps(registry()), encoding="utf-8")
            with patch.object(registration, "DshRpc") as rpc, patch.object(registration, "gateway_models") as gateway:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    result = registration.main(["--registry", str(source), "--dry-run"])
                self.assertEqual(result, 0)
                self.assertIn('"mode": "dry-run"', stdout.getvalue())
                rpc.assert_not_called()
                gateway.assert_not_called()


if __name__ == "__main__":
    unittest.main()
