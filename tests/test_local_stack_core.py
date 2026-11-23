"""CPU-only contract tests: no real model, proxy, WSL, GPU or external calls."""
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from local_stack.gateway import prepare_chat
from local_stack.registry import StackError, load_registry, normalize_context, validate_model
from local_stack.runtime import ModelRuntime, server_arguments


def fixture_model(root, model_id="saluki27b"):
    model = root / (model_id + ".gguf")
    model.write_bytes(b"GGUFfixture")
    engine = root / "llama-server.exe"
    engine.touch()
    return {"id": model_id, "name": model_id, "path": str(model), "engine": str(engine), "context": 8192, "context_presets": [4096, 8192], "max_output_tokens": 2048, "input": ["text"], "kv_cache": "q4_0", "batch_size": 128, "ubatch_size": 64, "gpu_layers": 99, "extra_args": [], "context_verified": True}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = fixture_model(self.root)
        self.registry = self.root / "config" / "models.json"
        self.registry.parent.mkdir()
        self.registry.write_text(json.dumps({"schema_version": 1, "models": [self.raw]}), encoding="utf-8")
        self.resources = Mock(return_value={"available_ram_gib": 8, "free_vram_mib": 16000, "gpu_utilization_percent": 0})
        self.runtime = ModelRuntime(self.registry, self.root / "state", resources=self.resources, identify=Mock(return_value=None), terminate=Mock())

    def tearDown(self):
        self.temporary.cleanup()

    def test_normalized_context(self):
        self.assertEqual(normalize_context(50000), 50176)
        self.assertEqual(normalize_context(150000), 150016)

    def test_relative_engine_resolves_against_repository_not_cwd(self):
        self.raw["engine"] = "llama-server.exe"
        self.registry.write_text(json.dumps({"schema_version": 1, "models": [self.raw]}))
        self.assertEqual(load_registry(self.registry)["models"][0]["engine"], str(self.root / "llama-server.exe"))

    def test_registry_rejects_launch_injection(self):
        for field, value in (("id", "../escape"), ("engine", str(self.root / "powershell.exe")), ("extra_args", ["--host", "0.0.0.0"]), ("gpu_layers", "99;bad"), ("reasoning_budget", -1), ("context_verified", "true")):
            with self.subTest(field=field), self.assertRaises(StackError):
                validate_model({**self.raw, field: value})

    def test_plan_has_loopback_and_no_secret_arguments(self):
        model = validate_model(self.raw)
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "secret-not-commandline"}):
            arguments = server_arguments(model, 8192, 18081)
        self.assertEqual(arguments[arguments.index("--host") + 1], "127.0.0.1")
        self.assertNotIn("--api-key", arguments)
        self.assertNotIn("secret-not-commandline", arguments)
        self.assertIn("--fit", arguments)
        self.assertIn("off", arguments)

    def test_auto_fit_omits_ngl_and_embeds_inkling_template_defaults(self):
        model = validate_model({**self.raw, "gpu_layers": "auto", "fit_target_mib": 2450, "reasoning_effort": "low", "reasoning_budget": 128, "temperature": 1.0})
        args = server_arguments(model, 8192, 18081)
        self.assertNotIn("-ngl", args)
        self.assertEqual(args[args.index("--fit-target") + 1], "2450")
        self.assertIn('{"reasoning_effort":"low"}', args)
        self.assertIn("--reasoning-budget", args)

    def test_admission_refuses_concurrency(self):
        self.runtime.acquire("inference")
        try:
            for action in (lambda: self.runtime.load("saluki27b"), lambda: self.runtime.configure("saluki27b", 4096), self.runtime.stop):
                with self.assertRaises(StackError) as error:
                    action()
                self.assertEqual(error.exception.status, 409)
        finally:
            self.runtime.release()

    def test_configure_persists_and_invalidates_old_context_evidence(self):
        result = self.runtime.configure("saluki27b", 5000)
        self.assertEqual(result["context"], 5120)
        self.assertFalse(self.runtime.all_models()[0]["context_verified"])
        reloaded = ModelRuntime(self.registry, self.root / "state", resources=self.resources)
        self.assertEqual(reloaded.find_model("saluki27b")["context"], 5120)

    def test_register_rejects_untrusted_engine_and_clears_evidence(self):
        second = fixture_model(self.root, "new-model")
        second["engine"] = str(self.root / "another" / "llama-server.exe")
        with self.assertRaises(StackError):
            self.runtime.register(second)
        second["engine"] = self.raw["engine"]
        result = self.runtime.register(second)["model"]
        self.assertEqual(result["validation"], "unverified")
        self.assertFalse(result["context_verified"])
        self.assertEqual(len(self.runtime.all_models()), 2)
        with self.assertRaises(StackError):
            self.runtime.register(second)

    def test_register_rejects_non_gguf_content(self):
        second = fixture_model(self.root, "broken")
        Path(second["path"]).write_bytes(b"NOPE")
        with self.assertRaises(StackError):
            self.runtime.register(second)

    def test_missing_target_preserves_existing_owned_model(self):
        self.runtime.record = {"pid": 42, "identity": {"executable": "a", "created": "1"}, "model": "old", "context": 8192}
        Path(self.raw["path"]).unlink()
        stop = self.runtime._stop_current = Mock()
        with self.assertRaises(StackError):
            self.runtime.ensure_loaded("saluki27b")
        stop.assert_not_called()

    def test_stale_pid_cannot_be_killed(self):
        self.runtime.record = {"pid": 42, "identity": {"executable": "old", "created": "1"}, "model": "saluki27b", "context": 8192}
        self.runtime.identify.return_value = {"executable": "new", "created": "2"}
        with self.assertRaises(StackError):
            self.runtime._stop_current()
        self.runtime.terminate.assert_not_called()

    def test_busy_backend_cannot_be_stopped(self):
        identity = {"executable": "owned", "created": "1"}
        self.runtime.record = {"pid": 42, "identity": identity, "model": "saluki27b", "context": 8192}
        self.runtime.identify.return_value = identity
        self.runtime._backend_idle = Mock(side_effect=StackError("busy", 409))
        with self.assertRaises(StackError):
            self.runtime._stop_current()
        self.runtime.terminate.assert_not_called()

    def test_startup_repeated_low_ram_aborts_only_new_process(self):
        model = validate_model(self.raw)
        child = Mock(pid=42)
        child.poll.return_value = None
        self.runtime.spawn = Mock(return_value=child)
        self.runtime.identify.return_value = {"executable": self.raw["engine"], "created": "1"}
        self.runtime._listener_present = Mock(return_value=False)
        self.runtime._check_health = Mock(side_effect=urllib.error.URLError("not ready"))
        self.runtime.sleep = Mock()
        self.resources.side_effect = [
            {"available_ram_gib": 8, "free_vram_mib": 16000, "gpu_utilization_percent": 0},
            {"available_ram_gib": 1, "free_vram_mib": 8000, "gpu_utilization_percent": 50},
            {"available_ram_gib": 1, "free_vram_mib": 8000, "gpu_utilization_percent": 50},
        ]
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "backend-key"}), self.assertRaises(StackError) as error:
            self.runtime._start(model, 8192)
        self.assertIn("during model loading", str(error.exception))
        self.runtime.terminate.assert_called_once()
        self.assertIsNone(self.runtime.record)
        self.assertEqual(self.runtime.spawn.call_args.kwargs["env"]["LLAMA_API_KEY"], "backend-key")

    def test_failed_switch_attempts_previous_profile_rollback(self):
        old = validate_model(self.raw)
        identity = {"executable": old["engine"], "created": "1"}
        self.runtime.record = {"pid": 42, "identity": identity, "model": "old-id", "context": 8192, "profile": old}
        def stop():
            self.runtime.record = None
        self.runtime._stop_current = Mock(side_effect=stop)
        self.runtime._start = Mock(side_effect=[StackError("target failed", 503), None])
        with self.assertRaises(StackError):
            self.runtime.ensure_loaded("saluki27b")
        self.assertEqual(self.runtime._start.call_count, 2)
        self.assertEqual(self.runtime._start.call_args.args, (old, 8192))

    def test_inference_cannot_inject_proxy_routing_or_remote_fetch(self):
        model = validate_model(self.raw)
        for field in ("api_base", "api_key", "custom_llm_provider", "metadata", "extra_headers", "base_url", "api_version"):
            with self.subTest(field=field), self.assertRaises(StackError):
                prepare_chat({"messages": [], field: "injected"}, model)
        image_model = {**model, "input": ["text", "image"]}
        remote = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://127.0.0.1/private"}}]}]}
        with self.assertRaises(StackError):
            prepare_chat(remote, image_model)
        inline = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw=="}}]}]}
        self.assertEqual(prepare_chat(inline, image_model)["max_tokens"], 2048)

    def test_cli_snapshot_flag_is_forwarded(self):
        from local_stack.__main__ import main
        with patch("local_stack.tui.run", return_value=0) as run:
            self.assertEqual(main(["ui", "--snapshot", "--registry", str(self.registry)]), 0)
        self.assertTrue(run.call_args.kwargs["snapshot"])
        self.assertEqual(run.call_args.kwargs["registry_path"], str(self.registry))

    def test_authenticated_http_redirects_disabled(self):
        from local_stack.runtime import NoRedirect
        import urllib.request
        request = urllib.request.Request("http://127.0.0.1/local", headers={"Authorization": "Bearer private"})
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://external.example")

    def test_configure_state_write_failure_is_transactional(self):
        with patch("local_stack.runtime.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.runtime.configure("saluki27b", 4096)
        self.assertEqual(self.runtime.find_model("saluki27b")["context"], 8192)

    def test_chat_output_budget_and_runtime_override_rejection(self):
        model = validate_model(self.raw)
        self.assertEqual(prepare_chat({"messages": []}, model)["max_tokens"], 2048)
        for body in ({"messages": [], "max_tokens": 999999}, {"messages": [], "n": 2}, {"messages": [], "model_path": "elsewhere"}, {"messages": [], "stream": "true"}):
            with self.assertRaises(StackError):
                prepare_chat(body, model)


if __name__ == "__main__":
    unittest.main()
