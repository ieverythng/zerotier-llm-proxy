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
        self.credential_ref = None
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
            self.credential_ref = args["ref"]
            return None
        if endpoint == "credentials/describe":
            return {ref: {"configured": self.credential_ref == ref} for ref in args["refs"]}
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

    def test_apply_merges_catalog_without_discarding_saved_rows_or_settings(self):
        desired = registration.build_provider(lock_registry(), "http://127.0.0.1:4141/v1")
        custom = {"id": "custom-local", "timeoutMs": 900000, "compat": {"keep": True}}
        long = {"id": "inkling2-mini-long", "contextWindow": 100096, "maxTokens": 4096,
                "input": ["text"], "timeoutMs": 600000, "compat": {"keep": True}}
        existing = {**desired, "models": [custom, long], "timeoutMs": 700000,
                    "headers": {"X-Custom": "keep"}}
        rpc = FakeRpc(copy.deepcopy(existing))
        default = {"ns": "agent-default-model", "revision": 3,
                   "value": {"provider": "watson-local", "model": "custom-local"}}
        describe = rpc.call

        def with_default(endpoint, args):
            result = describe(endpoint, args)
            if endpoint == "settings/describe":
                result["namespaces"].append(copy.deepcopy(default))
            return result

        with patch.object(rpc, "call", side_effect=with_default):
            result = registration.apply_registration(rpc, desired, "fixture-token")
            again = registration.apply_registration(rpc, desired, "fixture-token")
        saved = rpc.descriptor["value"]["providers"]["watson-local"]
        self.assertEqual(saved["timeoutMs"], existing["timeoutMs"])
        self.assertEqual(saved["headers"], existing["headers"])
        self.assertEqual(saved["models"][0], custom)
        self.assertEqual(saved["models"][1]["timeoutMs"], 600000)
        self.assertEqual(saved["models"][1]["compat"], {"keep": True})
        self.assertEqual(saved["models"][1]["maxTokens"], 10000)
        self.assertEqual(saved["models"][1]["input"], ["text", "image"])
        self.assertEqual(set(result["models"]), {row["id"] for row in desired["models"]})
        writes = [args for endpoint, args in rpc.calls if endpoint == "settings/mutate"]
        self.assertEqual([args["ns"] for args in writes], ["llm-pi-ai"])
        self.assertFalse(result["default_changed"])
        self.assertFalse(again["settings_changed"])

    def test_apply_preserves_existing_native_watson_credential_reference(self):
        desired = self.desired()
        existing = {**desired, "apiKeyEnv": "WATSON_LOCAL_API_KEY"}
        rpc = FakeRpc(existing)
        result = registration.apply_registration(rpc, desired, "fixture-token")
        self.assertFalse(result["settings_changed"])
        self.assertEqual(existing["apiKeyEnv"], "WATSON_LOCAL_API_KEY")
        self.assertEqual(rpc.credential_ref, "WATSON_LOCAL_API_KEY")

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

    def test_registration_snapshot_projects_known_metadata_without_nested_values(self):
        existing = self.desired()
        existing["models"][0].update(headers={"Authorization": "fixture-private"},
                                     reasoningEfforts={"custom": "fixture-private"})
        existing["models"].append({"id": "other", "name": {"private": "fixture-private"}})
        existing["baseURL"] += "?token=fixture-private"
        rpc = FakeRpc(existing)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            registration.apply_registration(rpc, self.desired(), "fixture-token", snapshot)
            content = snapshot.read_text()
        self.assertNotIn("fixture-private", content)
        self.assertNotIn("headers", content)
        public = json.loads(content)["managed_fields"]
        self.assertNotIn("baseURL", public)
        self.assertEqual(public["models"][0]["id"], "saluki27b")
        self.assertEqual(public["models"][0]["contextWindow"], 150016)

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


def lock_registry(*, vision=True, context=150016):
    value = registry()
    value["models"].append({"id": "inkling2-mini-long", "context": context,
        "minimum_context": context, "maximum_context": context, "context_presets": [context],
        "max_output_tokens": 10000, "context_verified": True, "input": ["text"]})
    if vision:
        value["models"][-1].update(input=["text", "image"], mmproj="fixture/projector.gguf", mmproj_gpu=False)
    return value


def lock_catalog(*, vision=True, context=150016):
    return {"data": [{"id": "inkling2-mini-long", "context_window": context,
        "max_output_tokens": 10000, "maxTokens": 10000, "contextWindow": context,
        "context_presets": [context], "context_verified": True,
        "input": ["text", "image"] if vision else ["text"]}]}


class LockRpc(FakeRpc):
    def __init__(self, default_model="hosted"):
        super().__init__({"api": "openai-completions", "baseURL": "http://127.0.0.1:4141/v1",
            "apiKeyEnv": "WATSON_LOCAL_API_KEY", "headers": {"Authorization": "secret-not-exported"},
            "models": [{"id": "saluki27b", "contextWindow": 150016, "maxTokens": 4096,
                        "compat": {"supportsStore": False}, "custom": "preserve"},
                       {"id": "inkling2-mini", "contextWindow": 8192, "maxTokens": 2048}]})
        self.default = {"ns": "agent-default-model", "revision": 3, "value": {
            "provider": "watson-local" if default_model.startswith("inkling") else "openai-codex",
            "model": default_model, "reasoningEffort": "high"}}
        self.reads = 0
        self.change_default_on_second_read = False

    def call(self, endpoint, args):
        if endpoint == "settings/describe":
            self.reads += 1
            if self.change_default_on_second_read and self.reads == 2:
                self.default["revision"] += 1
                self.default["value"] = {"provider": "watson-local", "model": "inkling2-mini"}
            result = super().call(endpoint, args)
            result["namespaces"].append(copy.deepcopy(self.default))
            return result
        return super().call(endpoint, args)


class CapacityLockTests(unittest.TestCase):
    def test_candidate_requires_exact_fixed_verified_live_contract(self):
        for source, field, bad in (("registry", "context", 100096), ("registry", "maximum_context", 150528),
                ("registry", "minimum_context", 8192), ("registry", "context_presets", [8192, 150016]),
                ("registry", "max_output_tokens", 4096), ("registry", "context_verified", False),
                ("live", "context_window", 8192), ("live", "context_verified", False),
                ("live", "max_output_tokens", 4096), ("live", "max_output_tokens", 12000),
                 ("live", "maxTokens", 4096), ("live", "contextWindow", 102400),
                 ("registry", "context_presets", [150016.0]), ("registry", "max_output_tokens", 10000.0),
                 ("live", "input", ["text"])):
            data, live = lock_registry(), lock_catalog()
            row = data["models"][-1] if source == "registry" else live["data"][0]
            row[field] = bad
            with self.subTest(source=source, field=field), self.assertRaises(registration.RegistrationError):
                registration.fixed_inkling_candidate(data, live)

    def test_matching_vision_metadata_advertises_image_with_existing_capacity(self):
        data, live = lock_registry(vision=True), lock_catalog(vision=True)
        candidate = registration.fixed_inkling_candidate(data, live)
        self.assertEqual((candidate["contextWindow"], candidate["maxTokens"], candidate["input"]),
                         (150016, 10000, ["text", "image"]))
        candidate["input"].clear()
        self.assertEqual(data["models"][-1]["input"], ["text", "image"])
        self.assertEqual(live["data"][0]["input"], ["text", "image"])

    def test_vision_requires_projector_declaration_and_matching_supported_modalities(self):
        for field, value in (("mmproj", None), ("mmproj", "fixture/projector.txt"),
                             ("input", ["image", "text"]), ("input", ["text", "text", "image"]),
                             ("input", ["text", "audio"])):
            data, live = lock_registry(vision=True), lock_catalog(vision=True)
            data["models"][-1][field] = value
            if field == "input":
                live["data"][0][field] = copy.deepcopy(value)
            with self.subTest(field=field, value=value), self.assertRaises(registration.RegistrationError):
                registration.fixed_inkling_candidate(data, live)
        with self.assertRaises(registration.RegistrationError):
            registration.fixed_inkling_candidate(lock_registry(vision=True), lock_catalog(vision=False))

    def test_duplicate_candidate_is_refused(self):
        data = lock_registry()
        data["models"].append(copy.deepcopy(data["models"][-1]))
        with self.assertRaises(registration.RegistrationError):
            registration.fixed_inkling_candidate(data, lock_catalog())

    def test_no_owner_settings_is_blocked_candidate_only(self):
        result = registration.capacity_lock_plan(lock_registry(), lock_catalog(), None)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["settings_verified"])
        self.assertNotIn("_after", result)

    def test_stale_default_is_never_overridden_by_acknowledgement(self):
        rpc = LockRpc("inkling2-mini")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            with self.assertRaisesRegex(registration.RegistrationError, "guard has no override"):
                registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot,
                                                 acknowledge_retire_short=True)
            self.assertFalse(snapshot.exists())
        self.assertFalse(any(endpoint != "settings/describe" for endpoint, _ in rpc.calls))

    def test_short_retirement_requires_ack_even_with_safe_default(self):
        rpc = LockRpc("inkling2-mini-long")
        plan = registration.capacity_lock_plan(lock_registry(), lock_catalog(), rpc.call("settings/describe", {}))
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any("active/saved sessions" in reason for reason in plan["blockers"]))

    def test_exact_unrelated_rows_and_native_credential_reference_are_preserved(self):
        rpc = LockRpc("inkling2-mini-long")
        before = copy.deepcopy(rpc.descriptor["value"]["providers"])
        default_before = copy.deepcopy(rpc.default)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            result = registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot,
                                                      acknowledge_retire_short=True)
            saved = json.loads(snapshot.read_text())
            self.assertNotIn("secret-not-exported", snapshot.read_text())
            self.assertNotIn("headers", snapshot.read_text())
            self.assertEqual(saved["models_public_before"][1]["id"], "inkling2-mini")
        after = rpc.descriptor["value"]["providers"]
        self.assertEqual(after["openai-codex"], before["openai-codex"])
        local = after["watson-local"]
        self.assertEqual(local["apiKeyEnv"], "WATSON_LOCAL_API_KEY")
        self.assertEqual(local["headers"], before["watson-local"]["headers"])
        self.assertEqual(local["models"][0], before["watson-local"]["models"][0])
        self.assertEqual([row["id"] for row in local["models"]], ["saluki27b", "inkling2-mini-long"])
        self.assertEqual(rpc.default, default_before)
        self.assertFalse(result["default_changed"])
        self.assertFalse(result["credentials_changed"])
        writes = [(endpoint, args) for endpoint, args in rpc.calls if endpoint != "settings/describe"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], "settings/mutate")
        self.assertEqual(writes[0][1]["expectedRevision"], 7)
        self.assertEqual(writes[0][1]["ops"][0]["path"], ["providers", "watson-local", "models"])

    def test_existing_long_row_raises_output_and_repairs_oversized_context_and_input(self):
        rpc = LockRpc("inkling2-mini-long")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        existing = {"id": "inkling2-mini-long", "name": "My Inkling", "contextWindow": 151000,
                    "maxTokens": 2048, "input": ["text"], "compat": {"supportsStore": False},
                    "custom": {"leave": "untouched"}}
        provider["models"][1] = copy.deepcopy(existing)
        before = copy.deepcopy(rpc.descriptor["value"])
        default_before = copy.deepcopy(rpc.default)
        with tempfile.TemporaryDirectory() as directory:
            result = registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), Path(directory) / "before.json")
        self.assertTrue(result["settings_changed"])
        before["providers"]["watson-local"]["models"][1] = {
            **existing, "contextWindow": 150016, "maxTokens": 4096, "input": ["text", "image"]}
        self.assertEqual(rpc.descriptor["value"], before)
        self.assertEqual(rpc.default, default_before)
        self.assertEqual([endpoint for endpoint, _ in rpc.calls if endpoint != "settings/describe"], ["settings/mutate"])

    def test_smaller_saved_context_is_preserved_with_explicit_10000_opt_in(self):
        rpc = LockRpc("inkling2-mini-long")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        existing = {"id": "inkling2-mini-long", "contextWindow": 32768, "maxTokens": 2048, "reasoningEfforts": False}
        provider["models"][1] = copy.deepcopy(existing)
        with tempfile.TemporaryDirectory() as directory:
            result = registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), Path(directory) / "one.json",
                                                      output=10000)
            again = registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), Path(directory) / "two.json",
                                                     output=10000)
        self.assertTrue(result["settings_changed"])
        self.assertFalse(again["settings_changed"])
        self.assertEqual(provider["models"][1], {**existing, "maxTokens": 10000, "input": ["text", "image"]})

    def test_vision_apply_updates_only_the_long_input_and_keeps_lower_context_and_full_output(self):
        rpc = LockRpc("inkling2-mini-long")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        existing = {"id": "inkling2-mini-long", "name": "Keep name", "contextWindow": 32768,
                    "maxTokens": 10000, "input": ["text"], "custom": {"keep": True}}
        provider["models"][1] = copy.deepcopy(existing)
        before = copy.deepcopy(rpc.descriptor["value"])
        default_before = copy.deepcopy(rpc.default)
        with tempfile.TemporaryDirectory() as directory:
            result = registration.apply_capacity_lock(rpc, lock_registry(vision=True), lock_catalog(vision=True),
                        Path(directory) / "before.json", output=10000)
            again = registration.apply_capacity_lock(rpc, lock_registry(vision=True), lock_catalog(vision=True),
                        Path(directory) / "again.json", output=10000)
        self.assertTrue(result["settings_changed"])
        self.assertFalse(again["settings_changed"])
        before["providers"]["watson-local"]["models"][1]["input"] = ["text", "image"]
        self.assertEqual(rpc.descriptor["value"], before)
        self.assertEqual(rpc.default, default_before)
        self.assertEqual([endpoint for endpoint, _ in rpc.calls if endpoint != "settings/describe"], ["settings/mutate"])

    def test_text_only_or_old_100k_main_backend_cannot_authorize_new_contract(self):
        for data, live, message in ((lock_registry(vision=False), lock_catalog(vision=False), "input modalities"),
                                    (lock_registry(context=100096), lock_catalog(context=100096), "must pin"),
                                    (lock_registry(), lock_catalog(context=100096), "live gateway must agree")):
            with self.subTest(message=message), self.assertRaisesRegex(registration.RegistrationError, message):
                registration.fixed_inkling_candidate(data, live)

    def test_registered_100k_fallback_and_its_default_are_preserved(self):
        rpc = LockRpc("inkling2-mini-100k")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        fallback = {"id": "inkling2-mini-100k", "contextWindow": 100096, "maxTokens": 10000,
                    "input": ["text", "image"], "custom": {"keep": True}}
        provider["models"].insert(1, copy.deepcopy(fallback))
        data = lock_registry()
        data["models"].append({**lock_registry(context=100096)["models"][-1], "id": fallback["id"]})
        live = lock_catalog()
        live["data"].append({**lock_catalog(context=100096)["data"][0], "id": fallback["id"]})
        default_before = copy.deepcopy(rpc.default)
        with tempfile.TemporaryDirectory() as directory:
            registration.apply_capacity_lock(rpc, data, live, Path(directory) / "before.json",
                                             acknowledge_retire_short=True, output=10000, context=150016)
        self.assertEqual(provider["models"][1], fallback)
        self.assertEqual(rpc.default, default_before)
        self.assertEqual([row["id"] for row in provider["models"]],
                         ["saluki27b", "inkling2-mini-100k", "inkling2-mini-long"])

    def test_gateway_listing_alone_does_not_create_fallback_row(self):
        rpc = LockRpc("inkling2-mini-long")
        live = lock_catalog()
        live["data"].append({**lock_catalog(context=100096)["data"][0], "id": "inkling2-mini-100k"})
        plan = registration.capacity_lock_plan(lock_registry(), live, rpc.call("settings/describe", {}),
                                               acknowledge_retire_short=True)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["model_ids_after"], ["saluki27b", "inkling2-mini-long"])

    def test_explicit_context_is_exact_and_widens_only_the_main_row(self):
        rpc = LockRpc("inkling2-mini-long")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        provider["models"][1] = {"id": "inkling2-mini-long", "contextWindow": 100096,
                                 "maxTokens": 10000, "input": ["text", "image"]}
        document = rpc.call("settings/describe", {})
        for invalid in (100096, 150017, 150016.0, True):
            with self.subTest(context=invalid), self.assertRaisesRegex(registration.RegistrationError, "Explicit Inkling context"):
                registration.capacity_lock_plan(lock_registry(), lock_catalog(), document, context=invalid)
        plan = registration.capacity_lock_plan(lock_registry(), lock_catalog(), document, context=150016, output=10000)
        self.assertEqual(plan["effective_context_window"], 150016)
        self.assertEqual(plan["_after"][1], {**provider["models"][1], "contextWindow": 150016})
        self.assertEqual(provider["models"][1]["contextWindow"], 100096)

    def test_inkling_context_flag_requires_lock_mode(self):
        with self.assertRaises(SystemExit) as error, patch("sys.stderr", new_callable=io.StringIO):
            registration.main(["--inkling-context", "150016"])
        self.assertEqual(error.exception.code, 2)

    def test_context_must_leave_input_space_without_silently_increasing_it(self):
        rpc = LockRpc("inkling2-mini-long")
        row = {"id": "inkling2-mini-long", "contextWindow": 8192, "maxTokens": 2048}
        rpc.descriptor["value"]["providers"]["watson-local"]["models"][1] = row
        for context, output in ((4096, 4096), (8192, 10000)):
            row["contextWindow"] = context
            with self.subTest(context=context, output=output), tempfile.TemporaryDirectory() as directory:
                snapshot = Path(directory) / "before.json"
                with self.assertRaisesRegex(registration.RegistrationError, "context must exceed"):
                    registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot, output=output)
                self.assertFalse(snapshot.exists())
        self.assertFalse(any(endpoint == "settings/mutate" for endpoint, _ in rpc.calls))

    def test_decreased_live_ceiling_aborts_before_snapshot_or_mutation(self):
        rpc = LockRpc("inkling2-mini-long")
        stale = lock_catalog()
        stale["data"][0].update(max_output_tokens=4096, maxTokens=4096)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            with self.assertRaisesRegex(registration.RegistrationError, "live gateway must agree"):
                registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot,
                                                 acknowledge_retire_short=True, refresh_advertised=lambda: stale)
            self.assertFalse(snapshot.exists())
        self.assertFalse(any(endpoint == "settings/mutate" for endpoint, _ in rpc.calls))

    def test_lost_live_vision_before_apply_aborts_without_snapshot_or_write(self):
        rpc = LockRpc("inkling2-mini-long")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            with self.assertRaisesRegex(registration.RegistrationError, "input modalities"):
                registration.apply_capacity_lock(rpc, lock_registry(vision=True), lock_catalog(vision=True), snapshot,
                         acknowledge_retire_short=True, refresh_advertised=lambda: lock_catalog(vision=False))
            self.assertFalse(snapshot.exists())
        self.assertFalse(any(endpoint == "settings/mutate" for endpoint, _ in rpc.calls))

    def test_idempotent_second_apply_does_not_write(self):
        rpc = LockRpc("inkling2-mini-long")
        with tempfile.TemporaryDirectory() as directory:
            registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), Path(directory) / "one.json",
                                              acknowledge_retire_short=True)
            result = registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), Path(directory) / "two.json")
        self.assertFalse(result["settings_changed"])
        self.assertEqual(sum(endpoint == "settings/mutate" for endpoint, _ in rpc.calls), 1)

    def test_racing_default_change_is_refused_before_write(self):
        rpc = LockRpc("inkling2-mini-long")
        rpc.change_default_on_second_read = True
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            with self.assertRaisesRegex(registration.RegistrationError, "changed during planning"):
                registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot,
                                                  acknowledge_retire_short=True)
            self.assertFalse(snapshot.exists())
        self.assertFalse(any(endpoint == "settings/mutate" for endpoint, _ in rpc.calls))

    def test_missing_default_namespace_fails_closed(self):
        document = FakeRpc().call("settings/describe", {})
        with self.assertRaisesRegex(registration.RegistrationError, "default-model"):
            registration.capacity_lock_plan(lock_registry(), lock_catalog(), document)

    def test_endpoint_mismatch_is_refused(self):
        rpc = LockRpc()
        rpc.descriptor["value"]["providers"]["watson-local"]["baseURL"] = "http://127.0.0.1:9999/v1"
        with self.assertRaisesRegex(registration.RegistrationError, "does not match"):
            registration.capacity_lock_plan(lock_registry(), lock_catalog(), rpc.call("settings/describe", {}))

    def test_existing_snapshot_is_not_overwritten(self):
        rpc = LockRpc("inkling2-mini-long")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "before.json"
            snapshot.write_text("keep")
            with self.assertRaises(FileExistsError):
                registration.apply_capacity_lock(rpc, lock_registry(), lock_catalog(), snapshot,
                                                  acknowledge_retire_short=True)
            self.assertEqual(snapshot.read_text(), "keep")
        self.assertFalse(any(endpoint == "settings/mutate" for endpoint, _ in rpc.calls))

    def test_public_plan_never_exports_private_model_lists(self):
        rpc = LockRpc("inkling2-mini-long")
        plan = registration.capacity_lock_plan(lock_registry(), lock_catalog(), rpc.call("settings/describe", {}),
                                               acknowledge_retire_short=True)
        output = json.dumps(registration.public_capacity_plan(plan))
        self.assertNotIn('"_before"', output)
        self.assertNotIn('"_after"', output)
        self.assertNotIn("secret-not-exported", output)
        self.assertNotIn("WATSON_LOCAL_API_KEY", output)

    def test_default_registration_includes_long_and_100k_vision_profiles(self):
        data = lock_registry()
        data["models"].append({**lock_registry(context=100096)["models"][-1],
                               "id": "inkling2-mini-100k", "name": "Inkling 100K fallback"})
        live = lock_catalog()
        live["data"].append({**lock_catalog(context=100096)["data"][0], "id": "inkling2-mini-100k"})
        for advertised in (None, live):
            with self.subTest(with_gateway=advertised is not None):
                plan = registration.build_provider(data, "http://127.0.0.1:4141/v1", advertised)
                self.assertEqual({row["id"]: (row["contextWindow"], row["maxTokens"], row["input"])
                                  for row in plan["models"] if row["id"].startswith("inkling")}, {
                    "inkling2-mini": (8192, 2048, ["text"]),
                    "inkling2-mini-long": (150016, 10000, ["text", "image"]),
                    "inkling2-mini-100k": (100096, 10000, ["text", "image"]),
                })

    def test_default_registration_rejects_disagreement_with_live_vision_profile(self):
        for live in ({"data": []}, lock_catalog(vision=False), lock_catalog(context=100096)):
            with self.subTest(catalog=live), self.assertRaises(registration.RegistrationError):
                registration.build_provider(lock_registry(), "http://127.0.0.1:4141/v1", live)

    def test_cli_unauthenticated_lock_plan_never_creates_dsh_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "models.json"
            source.write_text(json.dumps(lock_registry()))
            with patch.dict(registration.os.environ, {"LOCAL_STACK_API_KEY": "fake-only"}, clear=True), \
                    patch.object(registration, "gateway_models", return_value=lock_catalog()), \
                    patch.object(registration, "DshRpc") as rpc, \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = registration.main(["--registry", str(source), "--lock-inkling-capacity", "--dry-run"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "blocked")
            rpc.assert_not_called()

    def test_cli_applies_10000_only_with_owner_auth_and_matching_gateway(self):
        rpc = LockRpc("inkling2-mini-long")
        provider = rpc.descriptor["value"]["providers"]["watson-local"]
        provider["models"][1] = {"id": "inkling2-mini-long", "contextWindow": 32768, "maxTokens": 4096}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "registry.json"
            source.write_text(json.dumps(lock_registry()), encoding="utf-8")
            snapshot = Path(directory) / "before.json"
            args = ["--registry", str(source), "--lock-inkling-capacity", "--apply",
                    "--inkling-context", "150016", "--inkling-output", "10000", "--snapshot", str(snapshot)]
            with patch.dict(registration.os.environ, {"LOCAL_STACK_API_KEY": "fake-gateway"}, clear=True), \
                    patch.object(registration, "gateway_models", return_value=lock_catalog()), \
                    patch.object(registration, "DshRpc") as unauth_rpc, \
                    patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(registration.main(args), 1)
            unauth_rpc.assert_not_called()
            self.assertFalse(snapshot.exists())
            self.assertEqual(provider["models"][1]["maxTokens"], 4096)
            with patch.dict(registration.os.environ, {"LOCAL_STACK_API_KEY": "fake-gateway", "DSH_AUTH_COOKIE": "fake-owner"}, clear=True), \
                    patch.object(registration, "gateway_models", return_value=lock_catalog()) as gateway, \
                    patch.object(registration, "DshRpc", return_value=rpc), \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(registration.main(args), 0)
            self.assertEqual(gateway.call_count, 2)
            self.assertTrue(snapshot.exists())
        self.assertEqual(provider["models"][1],
                         {"id": "inkling2-mini-long", "contextWindow": 150016, "maxTokens": 10000, "input": ["text", "image"]})

    def test_cli_offline_snapshot_cannot_authorize_apply(self):
        with self.assertRaises(SystemExit) as error, patch("sys.stderr", new_callable=io.StringIO):
            registration.main(["--lock-inkling-capacity", "--apply", "--settings-view", "fake.json"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
