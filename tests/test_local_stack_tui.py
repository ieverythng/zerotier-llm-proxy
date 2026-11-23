"""No GPU, subprocesses, running gateway or extra test runner required."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_stack.tui import (
    ViewState,
    build_registration,
    choose_context,
    context_presets,
    discover_ggufs,
    format_tokens,
    normalize_models,
    read_registry,
    render_dashboard,
    run,
    registration_wizard,
    safe_error,
    safe_text,
    validate_context,
    validate_gguf,
)


class CatalogTests(unittest.TestCase):
    def test_catalog_normalizes_openai_and_runtime_overrides(self):
        registry = [{"id": "saluki27b", "name": "Saluki", "context": 100096, "engine": "approved", "context_presets": [50176, 100096, 150016]}]
        rows = normalize_models({"data": [{"id": "saluki27b", "context_window": 150016}]}, registry, {"models": [{"id": "saluki27b", "context": 50176}]})
        self.assertEqual(rows[0]["context"], 50176)
        self.assertEqual(rows[0]["engine"], "approved")
        self.assertEqual(rows[0]["context_presets"], [50176, 100096, 150016])
        self.assertEqual(registry[0]["context"], 100096)

    def test_historical_context_evidence_is_not_claimed_as_current_readiness(self):
        rows = normalize_models([], [{"id": "saluki", "context": 150016, "context_verified": True}])
        self.assertIn("Previously measured", rows[0]["validation"])
        self.assertIn("rechecked on load", rows[0]["validation"])

    def test_unknown_models_default_name_without_inventing_context(self):
        rows = normalize_models([{"id": "new-model"}])
        self.assertEqual(rows[0]["name"], "new-model")
        self.assertIsNone(rows[0]["context"])

    def test_malformed_remote_rows_do_not_break_catalog(self):
        self.assertEqual(normalize_models({"data": [None, {}, "bad"]}, status={"models": {}}), [])

    def test_preset_filtering(self):
        self.assertEqual(context_presets({"context": 8192, "context_presets": [4096, 8192, None, True, -1, "bad"]}), [4096, 8192])

    def test_context_never_silently_rounds_or_exceeds_profile(self):
        model = {"context": 8192, "context_presets": [8192, 16384]}
        self.assertEqual(validate_context("16384", model), 16384)
        for invalid in ("8193", 20000, -1, True, "1.5", "", 0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_context(invalid, model)

    def test_selection_is_stable_and_wraps(self):
        state = ViewState([{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}], {}, "a")
        state.move(-1)
        self.assertEqual(state.selected_id, "b")
        state.move(1)
        self.assertEqual(state.selected_id, "a")
        state.filter_text = "beta"
        self.assertEqual(state.selected["id"], "b")
        state.move(1)
        self.assertEqual(state.selected_id, "b")

    def test_text_control_sequences_are_literal_and_safe(self):
        self.assertEqual(safe_text("\x1b[31mred\x1b[0m\nnext\x00"), "rednext")
        self.assertEqual(safe_text("[bold]not markup[/bold]"), "[bold]not markup[/bold]")
        self.assertEqual(format_tokens(None), "—")
        self.assertEqual(format_tokens(150016), "150,016")

    def test_errors_do_not_display_environment_secrets(self):
        with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "secret-example-key"}):
            self.assertNotIn("secret-example-key", safe_error("API secret-example-key failed Bearer another-token"))
            self.assertNotIn("another-token", safe_error("Bearer another-token"))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "new-model.gguf"
        self.model.write_bytes(b"GGUF" + b"\0" * 32)
        self.template = {
            "id": "inkling2-mini", "name": "Inkling", "path": "existing.gguf",
            "engine": "approved/llama-server.exe", "context": 8192,
            "context_presets": [8192, 16384], "max_output_tokens": 2048,
            "input": ["text"], "extra_args": ["--no-op-offload"],
            "validation": "verified", "context_evidence": "old evidence", "verified_prompt_tokens": 7000,
        }

    def registration(self, **overrides):
        args = dict(model_id="new-model", name="New model", path=self.model, context=8192)
        args.update(overrides)
        return build_registration(self.template, **args)

    def test_registration_copies_only_approved_runtime_and_resets_evidence(self):
        result = self.registration()
        self.assertEqual(result["engine"], self.template["engine"])
        self.assertEqual(result["extra_args"], ["--no-op-offload"])
        self.assertEqual(result["validation"], "unverified")
        self.assertFalse(result["context_verified"])
        self.assertEqual(result["context_presets"], [8192])
        self.assertNotIn("context_evidence", result)
        self.assertNotIn("verified_prompt_tokens", result)
        self.assertEqual(self.template["validation"], "verified")

    def test_registration_matches_gateway_schema_and_drops_catalog_only_fields(self):
        from local_stack.registry import validate_model
        self.template.update(engine=str(self.root / "llama-server.exe"), maximum_context=262144, object="model", owned_by="gateway", tested_prompt_tokens=7000)
        result = self.registration()
        self.assertNotIn("object", result)
        self.assertNotIn("owned_by", result)
        self.assertNotIn("tested_prompt_tokens", result)
        self.assertEqual(result["maximum_context"], 8192)
        self.assertEqual(validate_model(result)["id"], "new-model")

    def test_new_artifact_cannot_inherit_large_context(self):
        with self.assertRaisesRegex(ValueError, "8,192"):
            self.registration(context=16384)

    def test_registration_rejects_duplicate_or_invalid_ids(self):
        for invalid in ("../unsafe", "UpperCase", "a;cmd", "", "a" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.registration(model_id=invalid)
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registration(existing_ids=["new-model"])

    def test_registration_rejects_unsafe_names_and_missing_engine(self):
        with self.assertRaises(ValueError):
            self.registration(name="bad\x1b[31m")
        self.template.pop("engine")
        with self.assertRaisesRegex(ValueError, "approved engine"):
            self.registration()

    def test_registration_rejects_partial_bad_or_projector_files(self):
        for name, content in (("partial.gguf.crdownload", b"GGUF"), ("bad.gguf", b"oops"), ("mmproj-test.gguf", b"GGUF")):
            artifact = self.root / name
            artifact.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_gguf(artifact)
        with self.assertRaises(ValueError):
            validate_gguf(self.root / "missing.gguf")

    def test_vision_registration_requires_new_matching_projector(self):
        self.template["input"] = ["text", "image"]
        self.template["mmproj"] = "old-mmproj.gguf"
        with self.assertRaisesRegex(ValueError, "matching image projector"):
            self.registration()
        projector = self.root / "mmproj-new.gguf"
        projector.write_bytes(b"GGUF")
        result = self.registration(mmproj=projector)
        self.assertEqual(result["mmproj"], str(projector.resolve()))

    def test_output_budget_is_bounded_by_initial_context(self):
        result = self.registration(context=1024)
        self.assertEqual(result["max_output_tokens"], 256)

    def test_discovery_is_bounded_and_excludes_projectors(self):
        (self.root / "mmproj-vision.gguf").write_bytes(b"GGUF")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "second.gguf").write_bytes(b"GGUF")
        deeper = nested / "deeper"
        deeper.mkdir()
        (deeper / "not-discovered.gguf").write_bytes(b"GGUF")
        found = discover_ggufs([self.root])
        self.assertEqual({path.name for path in found}, {"new-model.gguf", "second.gguf"})
        self.assertEqual(len(discover_ggufs([self.root], limit=1)), 1)

    def test_registry_read_missing_or_malformed_is_nonmutating(self):
        missing = self.root / "missing.json"
        self.assertEqual(read_registry(missing), [])
        self.assertFalse(missing.exists())
        malformed = self.root / "malformed.json"
        malformed.write_text("invalid", encoding="utf-8")
        self.assertEqual(read_registry(malformed), [])
        self.assertEqual(malformed.read_text(), "invalid")


class RenderTests(unittest.TestCase):
    def console(self, width=100):
        try:
            from rich.console import Console
        except ImportError:
            self.skipTest("Rich not installed; root stages terminal dependencies")
        return Console(file=io.StringIO(), width=width, force_terminal=False, color_system=None)

    def test_dashboard_shows_truthful_configured_and_loaded_context(self):
        console = self.console()
        state = ViewState(
            [{"id": "inkling2-mini", "name": "Inkling [not markup]", "context": 8192, "max_output_tokens": 2048, "input": ["text"], "validation": "provisional", "context_presets": [8192]}],
            {"active_model": "inkling2-mini", "context": 4096, "resources": {"free_vram_mib": 2048, "available_ram_gib": 3.5, "gpu_utilization_percent": 15}},
            connected=True,
        )
        console.print(render_dashboard(state, console.width))
        output = console.file.getvalue()
        for expected in ("WATSON", "8,192", "4,096", "provisional", "2.00 GiB free", "3.50 GiB free", "[not markup]", "LOADED"):
            self.assertIn(expected, output)

    def test_narrow_offline_dashboard_is_renderable(self):
        console = self.console(58)
        console.print(render_dashboard(ViewState([], {}, notice="offline"), 58))
        self.assertIn("OFFLINE", console.file.getvalue())

    def test_context_prompt_cancel_and_preset(self):
        console = self.console()
        with patch.object(console, "input", return_value=""):
            self.assertIsNone(choose_context(console, {"id": "x", "context": 8192}))
        with patch.object(console, "input", return_value="1"):
            self.assertEqual(choose_context(console, {"id": "x", "context": 8192}), 8192)

    def test_snapshot_fetches_without_loading_or_changing_anything(self):
        console = self.console()
        class Client:
            def __init__(self):
                self.calls = []
            def status(self):
                self.calls.append("status")
                return {"active_model": None, "busy": False}
            def models(self):
                self.calls.append("models")
                return [{"id": "test", "context_window": 8192}]
            def __getattr__(self, name):
                raise AssertionError("Mutating call: " + name)
        client = Client()
        self.assertEqual(run(client=client, console=console, registry_path="not-present.json", snapshot=True), 0)
        self.assertEqual(client.calls, ["status", "models"])

    def test_busy_keyboard_load_is_not_sent_and_quit_never_stops_stack(self):
        console = self.console()
        console._force_terminal = True
        class BusyClient:
            def __init__(self):
                self.calls = []
            def status(self):
                self.calls.append("status")
                return {"active_model": "test", "busy": True}
            def models(self):
                self.calls.append("models")
                return [{"id": "test", "context_window": 8192}]
            def load(self, *args):
                raise AssertionError("Unexpected load request")
            def stop(self):
                raise AssertionError("Quit must not stop the model")
            def __getattr__(self, name):
                raise AssertionError("Unexpected mutating call: " + name)
        class FakeKeys:
            def __init__(self):
                self.keys = iter(["enter", "q"])
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return next(self.keys)
        client = BusyClient()
        with patch("local_stack.tui.KeyReader", FakeKeys), patch("sys.stdin.isatty", return_value=True), patch("local_stack.tui.time.sleep"):
            self.assertEqual(run(client=client, console=console, registry_path="not-present.json", refresh_seconds=100), 0)
        self.assertEqual(client.calls, ["status", "models"])
        self.assertIn("left running", console.file.getvalue())

    def test_registration_wizard_cancel_never_requests_or_saves(self):
        console = self.console()
        with patch.object(console, "input", return_value=""):
            self.assertIsNone(registration_wizard(console, [{"id": "known", "engine": "approved"}], []))

    def test_offline_snapshot_has_actionable_start_command(self):
        console = self.console()
        class Offline:
            def status(self):
                raise ConnectionError("Connection refused")
        self.assertEqual(run(client=Offline(), console=console, registry_path="not-present.json", snapshot=True), 1)
        self.assertIn("Start-LocalModelStack.ps1", console.file.getvalue())


if __name__ == "__main__":
    unittest.main()
