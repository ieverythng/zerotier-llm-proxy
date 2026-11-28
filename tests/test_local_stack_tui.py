"""No GPU, subprocesses, running gateway or extra test runner required."""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from local_stack.tui import (
    ViewState,
    TerminalController,
    evidence_lines,
    benchmark_lines,
    flow_count,
    request_flow_view,
    request_flow_lines,
    render_request_flow,
    build_registration,
    choose_context,
    context_presets,
    grouped_model_rows,
    profile_context_options,
    choose_context_profile,
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

    def test_wizard_hides_unusable_profiles_and_reviews_confirmed_payload(self):
        from rich.console import Console
        output = io.StringIO()
        console = Console(file=output, width=100, color_system=None)
        oversized = {**self.template, "id": "too-long", "name": "Hidden long profile", "context": 100096, "minimum_context": 100096}
        with patch("local_stack.tui.discover_ggufs", return_value=[self.model]), patch(
            "local_stack.tui._read_choice", side_effect=["1", "MATCH", "1", "new-model", "New draft", "8192", "REGISTER"]
        ):
            result = registration_wizard(console, [oversized, self.template], [])
        self.assertEqual((result["id"], result["context"], result["context_verified"]), ("new-model", 8192, False))
        text = output.getvalue()
        self.assertNotIn("Hidden long profile", text)
        self.assertIn("Max output: 2,048", text)
        self.assertIn("does not load", text)

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


class LifecycleTerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = Path(self.temp.name) / "config" / "models.json"
        self.registry.parent.mkdir()
        self.rows = [{"id": "inkling2-mini", "name": "Inkling", "context": 8192, "context_presets": [4096, 8192], "maximum_context": 8192, "minimum_context": 4096, "max_output_tokens": 2048, "estimated_vram_mib": 12000}, {"id": "saluki27b", "name": "Saluki", "context": 8192, "context_presets": [8192], "max_output_tokens": 2048}]
        self.registry.write_text(json.dumps({"models": self.rows}), encoding="utf-8")
        class Client:
            def __init__(self, rows):
                self.rows, self.calls = rows, []
            def models(self):
                self.calls.append("models")
                return self.rows
            def benchmark(self, model, context=None, profile="quick"):
                self.calls.append(("benchmark", model, context, profile))
                return {"source": "measured", "status": "ok", "model": model, "context": context, "performance": {"prompt_tokens": 900, "output_tokens": 64, "prefill_tps": 123.4, "decode_tps": 45.6, "ttft_ms": 10}, "resources": {"minimum_free_vram_mib": 2400, "minimum_available_ram_gib": 3.1}, "quality": {"passed": True}}
        class Lifecycle:
            def __init__(self):
                self.running, self.busy, self.calls = False, False, []
            def status(self):
                return {"running": self.running, "owned": self.running, "state": "running" if self.running else "stopped", "busy": self.busy}
            def start(self, **kwargs):
                from local_stack.registry import StackError
                if self.busy:
                    raise StackError("busy, retry explicitly", 409)
                self.calls.append(("start", kwargs))
                self.running = True
                return {"status": "started", "message": "started"}
            def stop(self):
                from local_stack.registry import StackError
                if self.busy:
                    raise StackError("busy, retry explicitly", 409)
                self.calls.append(("stop",))
                self.running = False
                return {"status": "stopped", "message": "owned stack verified stopped"}
        self.client = Client(self.rows)
        self.lifecycle = Lifecycle()
        self.controller = TerminalController(self.client, self.lifecycle, self.registry)
        self.controller.state.selected_id = "inkling2-mini"

    def test_completed_background_snapshot_applies_without_reading_registry_on_input_thread(self):
        snapshot = self.controller.fetch()
        with patch("local_stack.tui.read_registry", side_effect=AssertionError("foreground registry read")):
            self.controller.apply_snapshot(snapshot)
        self.assertEqual(self.controller.state.selected["id"], "inkling2-mini")
        self.assertEqual(len(self.controller.state.models), 2)

    def test_historical_preview_is_reused_then_rechecked_for_a_new_context(self):
        with patch("local_stack.benchmark.benchmark_preview", return_value={"source": "configured"}) as preview, \
                patch("local_stack.tui.time.monotonic", return_value=123.0) as clock:
            self.controller.apply_snapshot(self.controller.fetch())
            self.controller.apply_snapshot(self.controller.fetch())
            self.assertEqual(preview.call_count, 2)
            self.controller.choose_context(4096)
            self.controller.apply_snapshot(self.controller.fetch())
            self.assertEqual(preview.call_count, 3)
            self.assertEqual(preview.call_args.args[1], 4096)
            clock.return_value += 11.0
            self.controller.apply_snapshot(self.controller.fetch())
            self.assertEqual(preview.call_count, 5)

    def test_offline_catalog_does_not_contact_gateway_or_start_stack(self):
        self.controller.apply_snapshot(self.controller.fetch())
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.lifecycle.calls, [])
        self.assertFalse(self.controller.state.connected)
        self.assertEqual(len(self.controller.state.models), 2)

    def test_stopped_supervisor_dashboard_still_shows_system_only_counters(self):
        from rich.console import Console
        from local_stack.lifecycle import StackLifecycle
        life = StackLifecycle(self.client, registry_path=self.registry, state_dir=Path(self.temp.name),
                              port_probe=lambda _: False)
        now = datetime(2026, 11, 25, 1, 30, tzinfo=timezone.utc)
        counters = {"cpu_utilization_percent": 25, "gpu_utilization_percent": 50,
                    "total_ram_gib": 32, "available_ram_gib": 16,
                    "total_vram_mib": 16384, "free_vram_mib": 4096}
        with patch("local_stack.runtime.resource_snapshot", return_value=counters), patch("local_stack.tui.datetime") as clock:
            controller = TerminalController(self.client, life, self.registry)
            clock.now.return_value = now
            controller.apply_snapshot(controller.fetch())
        output = io.StringIO()
        Console(file=output, width=100, color_system=None).print(render_dashboard(controller.state, 100, 25, resource_now=now))
        text = output.getvalue()
        for needle in ("SYSTEM ONLY", "CPU 25%", "GPU 50%", "RAM 50%", "VRAM 75%", "OFFLINE"):
            self.assertIn(needle, text)
        self.assertNotIn("● LOADED", text)
        self.assertFalse(controller.state.connected)
        self.assertEqual(self.client.calls, [])

    def test_external_supervisor_reconnect_requires_identity_and_generation(self):
        from local_stack.lifecycle import StackLifecycle
        from unittest.mock import Mock
        now = datetime(2026, 11, 25, 1, 30, tzinfo=timezone.utc)
        record = Path(self.temp.name) / "supervisor.json"
        identities = {12: "supervisor", 13: "gateway"}
        sampler = Mock(return_value={"gpu_utilization_percent": 36})
        fake_spawn = Mock(side_effect=AssertionError("polling must not launch a process"))
        client = Mock()
        client.status.return_value = {"active_model": "saluki27b", "context": 8192, "busy": True,
                                      "supervisor_generation": "old", "request_flow": {"version": 1}, "models": self.rows}
        life = StackLifecycle(client, registry_path=self.registry, state_dir=record.parent,
                              identify=identities.get, port_probe=lambda _: False, spawn=fake_spawn)
        controller = TerminalController(client, life, self.registry, resource_sampler=sampler, scratch_observer=lambda: None)
        controller.state.selected_id = "inkling2-mini"
        controller.choose_context(4096)
        supervisor = {"pid": 12, "identity": "supervisor", "generation": "new", "status": "starting", "stop_protocol": 1,
                      "children": [{"name": "gateway", "pid": 13, "identity": "gateway"}]}

        def poll(seconds):
            with patch("local_stack.tui.datetime") as clock:
                clock.now.return_value = now + timedelta(seconds=seconds)
                controller.apply_snapshot(controller.fetch())

        record.write_text(json.dumps(supervisor), encoding="utf-8")
        poll(0)
        self.assertEqual(controller.state.status["state"], "starting")
        self.assertEqual(controller.state.resource_history.latest(now).percentages["gpu"], 36)
        client.status.assert_not_called()
        record.write_text(json.dumps({**supervisor, "status": "ready"}), encoding="utf-8")
        poll(1)
        self.assertFalse(controller.state.connected)
        self.assertNotIn("active_model", controller.state.status)
        self.assertNotIn("request_flow", controller.state.status)
        client.status.return_value.update(supervisor_generation="new", resources_stale=False,
                                          resources={"gpu_utilization_percent": 56},
                                          resources_sampled_at=(now + timedelta(seconds=2)).isoformat())
        client.status.return_value.pop("request_flow")
        poll(2)
        self.assertTrue(controller.state.connected)
        self.assertEqual(controller.state.status["active_model"], "saluki27b")
        self.assertIn("Verified Watson gateway connected", controller.state.notice)
        self.assertEqual(controller.state.selected["id"], "inkling2-mini")
        self.assertEqual(controller.state.selected["context"], 4096)
        self.assertEqual([sample.source for sample in controller.state.resource_history.samples], ["system", "system", "gateway"])
        self.assertEqual(sampler.call_count, 2)
        fake_spawn.assert_not_called()
        client.load.assert_not_called()
        client.stop_stack.assert_not_called()

    def test_scratch_observation_is_separate_read_only_and_expires(self):
        from rich.console import Console
        now = datetime(2026, 11, 25, 1, 30, tzinfo=timezone.utc)
        advisory = {"source": "verified_scratch", "alias": "inkling2-mini", "context": 81920, "phase": "benchmark", "pid": 42, "ready": True,
                    "observed_at": now.isoformat()}
        controller = TerminalController(self.client, self.lifecycle, self.registry,
                                        resource_sampler=lambda: {"gpu_utilization_percent": 80}, scratch_observer=lambda: advisory)
        with patch("local_stack.tui.datetime") as clock:
            clock.now.return_value = now
            controller.apply_snapshot(controller.fetch())
        for width, height in ((64, 22), (110, 33)):
            for seconds in (0, 7):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(
                    render_dashboard(controller.state, width, height, resource_now=now + timedelta(seconds=seconds)))
                text = output.getvalue()
                self.assertEqual("VERIFIED SCRATCH · read-only" in text, seconds == 0)
                self.assertNotIn("● LOADED", text)
                self.assertIn("OFFLINE", text)
                self.assertLessEqual(len(text.splitlines()), height)
        self.assertNotIn("active_model", controller.state.status)
        self.assertNotIn("request_flow", controller.state.status)
        self.assertEqual(self.lifecycle.calls, [])
        self.assertEqual(self.client.calls, [])

    def test_choose_context_before_start_then_stop_and_restart_preserves_choice(self):
        self.controller.choose_context(4096)
        self.assertEqual(self.lifecycle.calls, [])
        self.controller.apply_result(self.controller.start_selected())
        self.controller.apply_snapshot(self.controller.fetch())
        self.assertTrue(self.controller.state.connected)
        self.controller.apply_result(self.lifecycle.stop())
        self.controller.apply_snapshot(self.controller.fetch())
        self.assertFalse(self.controller.state.connected)
        self.assertEqual(self.controller.state.selected["context"], 4096)
        self.controller.start_selected()
        starts = [call[1] for call in self.lifecycle.calls if call[0] == "start"]
        self.assertEqual(starts, [{"model": "inkling2-mini", "context": 4096, "direct": False}] * 2)

    def test_captured_start_selection_cannot_change_with_keyboard_navigation(self):
        selection = dict(self.controller.state.selected)
        self.controller.state.selected_id = "saluki27b"
        self.controller.start_selected(selection)
        self.assertEqual(self.lifecycle.calls[-1][1]["model"], "inkling2-mini")

    def test_busy_stop_refuses_and_does_not_schedule_later_work(self):
        self.lifecycle.running = True
        self.lifecycle.busy = True
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.lifecycle.stop()
        self.lifecycle.busy = False
        self.controller.apply_snapshot(self.controller.fetch())
        self.assertTrue(self.lifecycle.running)
        self.assertEqual(self.lifecycle.calls, [])

    def test_request_flow_receipt_uses_local_monotonic_time_and_clears_on_poll_failure(self):
        flow = RequestFlowTests().state().status["request_flow"]
        snapshot = ([], {"running": True, "state": "running", "models": self.rows, "request_flow": flow})
        with patch("local_stack.tui.time.monotonic", return_value=42.5):
            self.controller.apply_snapshot(snapshot)
        self.assertEqual(self.controller.state.flow_sampled_at, 42.5)
        self.assertTrue(request_flow_view(self.controller.state, 43)["fresh"])
        self.controller.poll_failed(ConnectionError("offline"))
        self.assertIsNone(self.controller.state.flow_sampled_at)
        self.assertFalse(any(node["active"] for node in request_flow_view(self.controller.state, 43)["nodes"]))

    def test_unavailable_poll_does_not_show_stale_model_as_active(self):
        self.controller.state.status = {"active_model": "inkling2-mini", "context": 8192, "resources": {"free_vram_mib": 9999}}
        self.controller.poll_failed(ConnectionError("gone"))
        self.assertFalse(self.controller.state.connected)
        self.assertNotIn("active_model", self.controller.state.status)
        self.assertNotIn("resources", self.controller.state.status)
        self.assertEqual(self.controller.state.status["state"], "unknown")

    def test_benchmark_only_calls_gateway_for_inkling_and_preserves_result(self):
        self.lifecycle.running = True
        self.controller.apply_snapshot(self.controller.fetch())
        result = self.controller.benchmark_selected("quick")
        self.controller.apply_result(result)
        self.assertEqual(self.client.calls[-1], ("benchmark", "inkling2-mini", 8192, "quick"))
        self.assertIs(self.controller.state.benchmark_result, result)
        self.controller.state.selected_id = "saluki27b"
        with self.assertRaisesRegex(ValueError, "Inkling-only"):
            self.controller.benchmark_selected("quick")

    def test_long_inkling_profile_allows_quick_but_not_near_context_benchmark(self):
        model = {**self.rows[0], "id": "inkling2-mini-long", "context": 100096}
        self.controller.state.connected = True
        self.controller.benchmark_selected("quick", model)
        self.assertEqual(self.client.calls[-1], ("benchmark", "inkling2-mini-long", 100096, "quick"))
        with self.assertRaisesRegex(ValueError, "Near-context benchmark"):
            self.controller.benchmark_selected("near-context", model)

    def test_memory_estimates_never_masquerade_as_measured_or_live(self):
        lines = evidence_lines(self.rows[0])
        self.assertIn("ESTIMATED", lines[0])
        self.assertIn("unavailable", lines[1])
        measured = {"source": "measured", "model": "inkling2-mini", "context": 4096, "resources": {"minimum_free_vram_mib": 2048, "minimum_available_ram_gib": 2}}
        self.assertIn("unavailable", evidence_lines(self.rows[0], measured)[1])
        measured["context"] = 8192
        self.assertIn("MEASURED minimum free", evidence_lines(self.rows[0], measured)[1])
        self.assertIn("not a fit guarantee", evidence_lines(self.rows[0], measured)[2])

    def test_results_show_actual_input_output_prefill_decode_ttft(self):
        result = self.client.benchmark("inkling2-mini", 8192)
        text = "\n".join(benchmark_lines(result))
        for expected in ("900", "64", "123.40", "45.60", "10.00", "PASS", "MEASURED"):
            self.assertIn(expected, text)

    def test_vision_quick_result_lists_bounded_case_metadata_not_raw_content(self):
        lines = benchmark_lines({"profile": "vision-quick", "model": "inkling2-mini-long", "context": 150016,
                                 "quality": {"passed": True, "passed_count": 1, "case_count": 1, "cases": [
                                     {"case": "ocr_digits", "modality": "image", "passed": True, "prompt_tokens": 66,
                                      "output_tokens": 82, "wall_ms": 1500.0, "content": "DO NOT DISPLAY"}]}})
        text = "\n".join(lines)
        self.assertIn("ocr_digits · image · PASS · 66 in/82 out", text)
        self.assertIn("1,500.00 ms wall", text)
        self.assertIn("1/1 synthetic cases passed", text)
        self.assertNotIn("DO NOT DISPLAY", text)

    def test_start_reports_context_catalog_refresh_from_nested_load_result(self):
        self.controller.apply_result({"status": "attached", "model": {"metadata_refresh_required": True}})
        self.assertIn("DSH model metadata needs refreshing", self.controller.state.notice)
        self.controller.apply_result({"status": "started", "model": {"metadata_refresh_required": False}})
        self.assertNotIn("metadata needs refreshing", self.controller.state.notice)

    def test_uncertain_stop_is_not_rendered_as_success(self):
        self.controller.apply_result({"status": "uncertain", "message": "Unable to verify all owned ports closed"})
        self.assertIn("Unable to verify", self.controller.state.notice)
        self.assertNotIn("completed", self.controller.state.notice)

    def test_observatory_history_is_discoverable_and_read_only_even_while_busy(self):
        from concurrent.futures import Future
        from contextlib import nullcontext
        from local_stack import tui
        from rich.console import Console

        class Keys:
            def __enter__(self):
                self.keys = iter(("v", "q"))
                return self
            def __exit__(self, *_):
                pass
            def read(self):
                return next(self.keys)
            def cooked(self):
                return nullcontext()

        observation = Future()
        observation.set_result(([], {"state": "running", "running": True, "busy": True, "models": self.rows}))
        console = Console(file=io.StringIO(), force_terminal=True, width=115, color_system=None)
        with patch.object(tui, "KeyReader", Keys), patch("sys.stdin.isatty", return_value=True), \
                patch.object(tui, "_background", return_value=observation) as work, patch.object(tui, "_read_choice", return_value=""):
            self.assertEqual(run(client=self.client, lifecycle=self.lifecycle, registry_path=self.registry, console=console), 0)
        text = console.file.getvalue()
        for expected in ("WATSON OBSERVATORY", "V History", "QUALIFICATION HISTORY (READ-ONLY)", "147,968", "139,742", "556.42", "not live readiness"):
            self.assertIn(expected, text)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.lifecycle.calls, [])
        work.assert_called_once()

    def test_detach_key_does_not_wait_for_an_extra_paint(self):
        from rich.console import Console
        from local_stack import tui
        from unittest.mock import Mock
        from concurrent.futures import Future
        keys = Mock()
        keys.__enter__ = Mock(return_value=keys)
        keys.__exit__ = Mock(return_value=False)
        keys.read.return_value = "q"
        console = Console(file=io.StringIO(), force_terminal=True, width=120, color_system=None)
        with patch.object(tui, "KeyReader", return_value=keys), patch("sys.stdin.isatty", return_value=True), \
                patch.object(tui, "_background", return_value=Future()), patch.object(tui, "render_dashboard", wraps=tui.render_dashboard) as render:
            self.assertEqual(run(client=self.client, lifecycle=self.lifecycle, registry_path=self.registry, console=console), 0)
        keys.read.assert_called_once()
        self.assertEqual(render.call_count, 1)
        self.assertEqual(self.lifecycle.calls, [])

    def test_slow_initial_status_does_not_freeze_detach(self):
        from rich.console import Console
        gate = threading.Event()
        started = threading.Event()
        lifecycle = self.lifecycle
        def slow_status():
            started.set()
            gate.wait(3)
            return {"running": False, "state": "stopped"}
        lifecycle.status = slow_status
        class Keys:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return "q"
        console = Console(file=io.StringIO(), force_terminal=True, width=120, color_system=None)
        try:
            before = time.monotonic()
            with patch("local_stack.tui.KeyReader", Keys), patch("sys.stdin.isatty", return_value=True):
                self.assertEqual(run(client=self.client, lifecycle=lifecycle, registry_path=self.registry, console=console), 0)
            self.assertLess(time.monotonic() - before, 0.75)
            self.assertTrue(started.is_set())
            self.assertEqual(self.lifecycle.calls, [])
            self.assertIn("detached", console.file.getvalue())
        finally:
            gate.set()

    def test_cli_forwards_explicit_direct_mode_without_starting_anything(self):
        from local_stack.__main__ import main
        with patch("local_stack.tui.run", return_value=0) as ui:
            self.assertEqual(main(["ui", "--direct", "--snapshot"]), 0)
        self.assertTrue(ui.call_args.kwargs["direct"])
        self.assertTrue(ui.call_args.kwargs["snapshot"])

    def test_stale_system_samples_are_not_labelled_live(self):
        from rich.console import Console
        output = io.StringIO()
        console = Console(file=output, width=120, color_system=None)
        state = ViewState(self.rows, {"state": "running", "resources_stale": True, "resources": {"free_vram_mib": 2048, "available_ram_gib": 2}}, connected=True)
        console.print(render_dashboard(state, 120, 35))
        self.assertIn("STALE SYSTEM SAMPLE", output.getvalue())
        self.assertNotIn("LIVE SYSTEM RESOURCES", output.getvalue())

    def test_large_catalog_keeps_selection_and_controls_in_the_viewport(self):
        from rich.console import Console
        models = [{"id": f"m{i}", "name": f"Fixture model {i}", "context": 8192} for i in range(30)]
        state = ViewState(models, {}, selected_id="m24")
        for width, height in ((64, 22), (80, 24), (120, 36)):
            with self.subTest(width=width, height=height):
                out = io.StringIO()
                Console(file=out, width=width, color_system=None).print(render_dashboard(state, width, height))
                frame = out.getvalue()
                self.assertIn("Fixture model 24", frame)
                self.assertIn("/30", frame)
                self.assertIn("Q Detach", frame)
                self.assertLessEqual(len(frame.splitlines()), height)

    def test_compact_frames_fit_common_terminal_sizes(self):
        from rich.console import Console
        models = [dict(row) for row in self.rows]
        models[0]["validation"] = "Measured short canary; longer context and tools require qualification. " * 3
        state = ViewState(models, {"state": "stopped", "running": False}, selected_id="inkling2-mini", notice="Select profile and context, then Enter to start")
        for width, height in ((120, 30), (80, 24)):
            with self.subTest(width=width, height=height):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(render_dashboard(state, width, height))
                self.assertLessEqual(len(output.getvalue().splitlines()), height)
                self.assertIn("Stop STACK", output.getvalue())

    def test_near_context_benchmark_is_not_offered_for_unqualified_large_allocations(self):
        self.controller.state.connected = True
        selected = dict(self.controller.state.selected)
        selected["context"] = 16384
        with self.assertRaisesRegex(ValueError, "8,192"):
            self.controller.benchmark_selected("near-context", selected)
        self.assertEqual(self.client.calls, [])

    def test_resource_history_ingests_each_authoritative_snapshot_once(self):
        timestamp = "2026-11-24T03:00:00+00:00"
        status = {"running": True, "state": "running", "models": self.rows, "resources_stale": False, "resources_sampled_at": timestamp, "resources": {"cpu_utilization_percent": None, "gpu_utilization_percent": 50, "total_ram_gib": 16, "available_ram_gib": 4, "total_vram_mib": 16384, "free_vram_mib": 4096}}
        with patch("local_stack.tui.datetime") as clock:
            clock.now.return_value = datetime.fromisoformat(timestamp)
            self.controller.apply_snapshot(([], status))
            self.controller.apply_snapshot(([], status))
        history = self.controller.state.resource_history
        self.assertEqual(len(history.samples), 1)
        self.assertIsNone(history.samples[-1].percentages["cpu"])
        self.assertEqual(history.samples[-1].percentages["ram"], 75)
        self.controller.poll_failed(ConnectionError("gone"))
        self.assertEqual(history.observation, "offline")
        self.assertEqual(len(history.samples), 1)
        self.assertEqual(self.client.calls, [])

    def test_apply_snapshot_marks_observed_gateway_stage_at_its_own_timestamp(self):
        now = datetime(2026, 11, 24, 3, 0, 2, tzinfo=timezone.utc)
        resource_time = now - timedelta(seconds=2)
        flow = RequestFlowTests().state().status["request_flow"]
        flow["observed_at"] = now.isoformat()
        flow["active"] = [{"id": "req-1", "event_seq": 3, "stage": "forward_wait", "source": "observed"}]
        status = {"running": True, "state": "running", "models": self.rows, "generation": "generation-a",
                  "resources_stale": False, "resources_sampled_at": resource_time.isoformat(),
                  "resources": {"gpu_utilization_percent": 50}, "request_flow": flow}
        with patch("local_stack.tui.datetime") as clock:
            clock.now.return_value = now
            self.controller.apply_snapshot(([], status))
        history = self.controller.state.resource_history
        self.assertEqual(history.samples[-1].timestamp, resource_time)
        self.assertEqual(history.gateway_marks[-1].timestamp, now)
        self.assertEqual(history.gateway_marks[-1].stage, "forward_wait")
        self.controller.poll_failed(ConnectionError("gone"))
        self.assertEqual(history.gateway_status(now), "offline")
        self.assertEqual(self.lifecycle.calls, [])

    def test_dashboard_keeps_compact_telemetry_visible_without_hiding_model_library(self):
        from rich.console import Console
        state = self.controller.state
        state.connected = True
        now = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
        state.status["resources_stale"] = False
        state.resource_history.observe({"resources_stale": False, "resources_sampled_at": now.isoformat(),
            "resources": {"cpu_utilization_percent": 25, "gpu_utilization_percent": 50, "total_ram_gib": 16,
                          "available_ram_gib": 4, "total_vram_mib": 16384, "free_vram_mib": 4096}}, connected=True, now=now)
        for width, height in ((115, 30), (80, 24)):
            with self.subTest(width=width):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(render_dashboard(state, width, height, 10, resource_now=now))
                text = output.getvalue()
                for expected in ("CPU 25%", "GPU 50%", "RAM 75%", "VRAM 75%", "MODEL LIBRARY", "Z Zoom", "? Help"):
                    self.assertIn(expected, text)
                self.assertLessEqual(len(text.splitlines()), height)

    def test_resource_chart_focus_fits_wide_and_narrow_without_losing_controls(self):
        from rich.console import Console
        state = self.controller.state
        state.show_resource_charts = True
        now = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
        for seconds in range(0, 121, 2):
            state.resource_history.observe({"resources_stale": False, "resources_sampled_at": (now + timedelta(seconds=seconds)).isoformat(), "resources": {"cpu_utilization_percent": seconds % 100, "gpu_utilization_percent": 50, "total_ram_gib": 16, "available_ram_gib": 4, "total_vram_mib": 16384, "free_vram_mib": 4096}}, connected=True)
        for width, height in ((115, 30), (80, 24), (64, 22)):
            with self.subTest(width=width):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(render_dashboard(state, width, height, 10, resource_now=now + timedelta(seconds=120)))
                text = output.getvalue()
                self.assertLessEqual(len(text.splitlines()), height)
                for expected in ("CPU", "GPU", "RAM", "VRAM", "GiB", "120s", "SYSTEM-WIDE", "Stop STACK", "H Charts", "Q Detach", "FLOW"):
                    self.assertIn(expected, text)

    def test_default_powershell_opens_terminal_before_legacy_start_check(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/windows/Start-LocalModelStack.ps1").read_text(encoding="utf-8")
        self.assertLess(script.index("if (-not $NoUi)"), script.index("--check @modeArguments"))
        self.assertIn("'local_stack', 'ui'", script)
        self.assertIn("if ($Serve)", script)
        self.assertIn("if ($Direct) { $uiArguments += '--direct' }", script)


class RequestFlowTests(unittest.TestCase):
    def state(self, **flow_updates):
        flow = {
            "version": 1, "generation": "generation-a", "route": "headroom_litellm",
            "counter_scope": "inference_and_benchmark_posts", "observed_at": "2026-11-24T02:00:00Z",
            "counters": {"ingress": 17, "accepted": 11, "active": 1, "completed": 8, "failed": 1, "rejected": 6, "cancelled": 1},
            "active": [{"id": 17, "model": "inkling2-mini", "kind": "inference", "stage": "forward_wait", "source": "observed", "elapsed_ms": 123, "bytes_forwarded": 42, "first_content_ms": None}],
            "last": None,
            "nodes": {"gateway": {"state": "active", "source": "observed"}, "headroom": {"state": "in_flight", "source": "inferred"}, "litellm": {"state": "in_flight", "source": "inferred"}},
            "backend": {"source": "authenticated_metrics", "state": "processing", "observed_at": "2026-11-24T02:00:00Z", "stale": False, "epoch": "worker-a", "processing": 1, "prompt_tokens_total": 2000, "generated_tokens_total": 100},
        }
        flow.update(flow_updates)
        return ViewState([{"id": "inkling2-mini", "name": "Inkling", "context": 8192, "max_output_tokens": 2048}], {"state": "running", "running": True, "request_flow": flow}, connected=True, flow_sampled_at=10.0)

    def render(self, renderable, width=120):
        from rich.console import Console
        output = io.StringIO()
        Console(file=output, width=width, color_system=None).print(renderable)
        return output.getvalue()

    def test_four_boxes_preserve_evidence_and_gateway_counter_labels(self):
        state = self.state()
        output = self.render(render_request_flow(state, 120, 40, 10.1))
        for expected in ("Gateway", "Headroom", "LiteLLM", "llama.cpp", "OBS", "INFER", "in 17", "accepted 11", "active 1", "done 8", "reject 6", "cancel 1", "DSH compaction unobserved"):
            self.assertIn(expected, output)
        self.assertNotIn("compressing", output.lower())

    def test_missing_flow_is_unknown_not_fabricated_zero_or_activity(self):
        state = self.state()
        state.status.pop("request_flow")
        view = request_flow_view(state, 10.1)
        self.assertEqual(view["counters"], {})
        self.assertTrue(all(node["label"] == "UNKNOWN" and not node["active"] for node in view["nodes"]))
        self.assertEqual(flow_count(None), "—")
        for invalid in (True, -1, "7", float("nan")):
            self.assertEqual(flow_count(invalid), "—")

    def test_stale_poll_sample_freezes_all_animation(self):
        state = self.state()
        view = request_flow_view(state, 16.1)
        self.assertFalse(view["fresh"])
        self.assertTrue(all(not node["active"] and not node["pulse"] for node in view["nodes"]))
        output = self.render(render_request_flow(state, 120, 40, 16.1))
        self.assertIn("Gateway LAST sample", output)
        self.assertIn("animation frozen", output)

    def test_unknown_receipt_time_never_animates(self):
        state = self.state()
        state.flow_sampled_at = None
        self.assertFalse(any(node["active"] for node in request_flow_view(state, 10.1)["nodes"]))

    def test_monotonic_pulse_changes_only_with_fresh_reported_activity(self):
        state = self.state()
        first = request_flow_view(state, 10.01)["nodes"]
        later = request_flow_view(state, 10.2)["nodes"]
        self.assertNotEqual(first[0]["pulse"], later[0]["pulse"])
        self.assertEqual(first[1]["evidence"], "inferred")
        self.assertTrue(first[1]["active"])
        self.assertEqual(first[2]["pulse"], first[1]["pulse"])

    def test_stale_backend_metrics_do_not_animate_or_invent_compute_phase(self):
        state = self.state()
        state.status["request_flow"]["backend"]["stale"] = True
        nodes = request_flow_view(state, 10.1)["nodes"]
        self.assertTrue(nodes[0]["active"])
        self.assertFalse(nodes[3]["active"])
        self.assertEqual(nodes[3]["kind"], "stale")
        self.assertIn("backend totals (stale)", self.render(render_request_flow(state, 120, 40, 10.1)))

    def test_never_observed_backend_is_unknown_not_a_stale_measurement(self):
        state = self.state()
        state.status["request_flow"]["backend"].update(state="unknown", stale=True, observed_at=None, epoch=None, processing=None, prompt_tokens_total=None, generated_tokens_total=None)
        node = request_flow_view(state, 10.1)["nodes"][3]
        self.assertEqual(node["label"], "UNKNOWN")
        self.assertFalse(node["active"])
        self.assertIn("backend totals (unknown)", self.render(render_request_flow(state, 120, 40, 10.1)))

    def test_backend_activity_requires_authenticated_metrics_not_gpu_utilization(self):
        state = self.state()
        state.status["request_flow"]["backend"]["source"] = "gpu_utilization"
        self.assertEqual(request_flow_view(state, 10.1)["nodes"][3]["label"], "UNKNOWN")

    def test_unknown_proxy_route_never_infers_hops_and_compact_provenance_stays_visible(self):
        state = self.state(route="unsupported")
        nodes = request_flow_view(state, 10.1)["nodes"]
        self.assertTrue(all(node["label"] == "UNKNOWN" and not node["active"] for node in nodes[1:3]))
        output = self.render(render_request_flow(self.state(), 80, 24, 10.1), 80)
        self.assertIn("~inferred", output)
        self.assertIn("in17", output)
        self.assertIn("end8", output)

    def test_direct_route_bypasses_proxy_boxes(self):
        state = self.state(route="direct")
        nodes = request_flow_view(state, 10.1)["nodes"]
        self.assertTrue(all(node["kind"] == "bypassed" and not node["active"] for node in nodes[1:3]))
        self.assertIn("DIRECT: HR/LL bypassed", self.render(render_request_flow(state, 120, 40, 10.1)))

    def test_native_benchmark_bypasses_proxies_even_for_configured_proxy_route(self):
        state = self.state()
        state.status["request_flow"]["active"][0].update(kind="benchmark", stage="benchmark")
        nodes = request_flow_view(state, 10.1)["nodes"]
        self.assertTrue(all(node["kind"] == "bypassed" for node in nodes[1:3]))
        self.assertIn("Benchmark: HR/LL bypassed", self.render(render_request_flow(state, 120, 40, 10.1)))

    def test_proxy_state_with_unknown_provenance_is_not_animated(self):
        state = self.state()
        state.status["request_flow"]["nodes"]["headroom"]["source"] = "unknown"
        node = request_flow_view(state, 10.1)["nodes"][1]
        self.assertEqual(node["label"], "UNKNOWN")
        self.assertFalse(node["active"])

    def test_idle_and_offline_never_fake_progress(self):
        state = self.state(active=[])
        for entry in state.status["request_flow"]["nodes"].values():
            entry["state"] = "idle"
        state.status["request_flow"]["backend"].update(state="idle", processing=0)
        self.assertFalse(any(node["active"] for node in request_flow_view(state, 10.1)["nodes"]))
        state.connected = False
        state.status["state"] = "stopped"
        self.assertTrue(all(node["label"] == "OFFLINE" and not node["active"] for node in request_flow_view(state, 10.1)["nodes"]))

    def test_evidence_reports_first_actual_content_and_backend_counter_epoch(self):
        state = self.state()
        lines = "\n".join(request_flow_lines(state, 10.1))
        self.assertIn("First actual answer content: unavailable", lines)
        self.assertIn("forwarded bytes 42", lines)
        self.assertIn("prompt 2,000 / generated 100", lines)
        self.assertIn("epoch worker-a", lines)
        self.assertIn("DSH compaction: unobserved", lines)
        state.status["request_flow"]["active"][0]["first_content_ms"] = 234.5
        self.assertIn("234.50 ms", "\n".join(request_flow_lines(state, 10.1)))

    def test_new_generation_counters_are_not_added_to_previous_process(self):
        state = self.state()
        self.assertEqual(request_flow_view(state, 10.1)["counters"]["ingress"], 17)
        state.status["request_flow"].update(generation="generation-b", counters={"ingress": 1, "active": 0})
        self.assertEqual(request_flow_view(state, 10.1)["counters"]["ingress"], 1)

    def test_flow_keeps_compact_controls_visible(self):
        from rich.console import Console
        state = self.state()
        state.models += [{"id": "inkling2-mini-long", "name": "Inkling 2 Mini · 100K profile", "context": 100096}, {"id": "saluki27b", "name": "Underdog Saluki 27B", "context": 150016}]
        for width, height in ((115, 30), (80, 24)):
            with self.subTest(width=width, height=height):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(render_dashboard(state, width, height, 10.1))
                self.assertLessEqual(len(output.getvalue().splitlines()), height)
                self.assertIn("FLOW" if width < 110 else "Gateway", output.getvalue())
                self.assertIn("Stop STACK", output.getvalue())
                self.assertIn("Q Detach", output.getvalue())


class InklingGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        artifact = self.root / "shared.gguf"
        artifact.write_bytes(b"GGUF")
        self.rows = [
            {"id": "inkling2-mini", "name": "Inkling · shared artifact", "path": str(artifact), "engine": str(self.root / "short" / "llama-server.exe"), "context": 8192, "context_presets": [4096, 8192], "minimum_context": 4096, "maximum_context": 8192, "max_output_tokens": 2048, "estimated_vram_mib": 12400, "validation": "short profile evidence"},
            {"id": "inkling2-mini-long", "name": "Inkling · 100K", "path": str(self.root) + "/nested/../shared.gguf", "engine": str(self.root / "long" / "llama-server.exe"), "context": 100096, "context_presets": [100096], "minimum_context": 100096, "maximum_context": 100096, "max_output_tokens": 2048, "estimated_vram_mib": 14500, "validation": "long profile evidence"},
            {"id": "saluki27b", "name": "Saluki", "context": 150016, "context_presets": [150016], "max_output_tokens": 4096},
        ]
        self.registry = self.root / "config" / "models.json"
        self.registry.parent.mkdir()
        self.registry.write_text(json.dumps({"models": self.rows}), encoding="utf-8")
        class Lifecycle:
            def __init__(self):
                self.calls = []
            def start(self, **kwargs):
                self.calls.append(kwargs)
                return {"status": "attached"}
        class Client:
            def __init__(self):
                self.calls = []
            def benchmark(self, model, context=None, profile="quick"):
                self.calls.append((model, context, profile))
                return {}
        self.lifecycle, self.client = Lifecycle(), Client()
        self.controller = TerminalController(self.client, self.lifecycle, self.registry)
        self.controller.state.selected_id = "inkling2-mini"

    def console(self):
        from rich.console import Console
        return Console(file=io.StringIO(), width=115, color_system=None)

    def test_matching_artifact_groups_only_display_not_source_profiles(self):
        before = json.dumps(self.rows, sort_keys=True)
        rows = grouped_model_rows(self.rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "inkling2-mini")
        self.assertEqual(len(rows[0]["_variants"]), 2)
        self.assertEqual(json.dumps(self.rows, sort_keys=True), before)
        self.assertEqual(len(self.controller.state.models), 3)
        self.assertTrue(all("_variants" not in row for row in self.controller.state.models))

    def test_missing_alias_or_different_artifact_never_groups(self):
        self.assertEqual(len(grouped_model_rows(self.rows[:1])), 1)
        self.assertNotIn("_variants", grouped_model_rows(self.rows[:1])[0])
        mismatched = [dict(row) for row in self.rows]
        mismatched[1]["path"] = str(self.root / "different.gguf")
        self.assertEqual(len(grouped_model_rows(mismatched)), 3)
        mismatched[1].pop("path")
        self.assertEqual(len(grouped_model_rows(mismatched)), 3)

    def test_short_mode_remains_default_even_when_long_name_sorts_first(self):
        fresh = TerminalController(self.client, self.lifecycle, self.registry)
        self.assertEqual(fresh.state.models[0]["id"], "inkling2-mini-long")
        self.assertEqual(fresh.state.selected["id"], "inkling2-mini")
        self.assertEqual(fresh.state.selected["context"], 8192)
        self.assertEqual(self.lifecycle.calls, [])

    def test_promoted_150k_vision_is_selected_without_autoload_and_100k_fallback_is_distinct(self):
        base, former_long, saluki = self.rows
        fallback = {**former_long, "id": "inkling2-mini-100k", "input": ["text", "image"], "mmproj": str(self.root / "projector.gguf")}
        promoted = {**fallback, "id": "inkling2-mini-long", "context": 150016, "minimum_context": 150016,
                    "maximum_context": 150016, "context_presets": [150016], "max_output_tokens": 10000,
                    "experimental_resources": {"minimum_free_vram_mib": 512, "minimum_available_ram_gib": 1.25}}
        self.registry.write_text(json.dumps({"models": [base, promoted, fallback, saluki]}), encoding="utf-8")
        controller = TerminalController(self.client, self.lifecycle, self.registry)
        self.assertEqual(controller.state.selected["id"], "inkling2-mini-long")
        self.assertEqual(len(controller.state.visible_models), 2)
        self.assertEqual(profile_context_options(controller.state.selected_variants), [("inkling2-mini", 4096),
                         ("inkling2-mini", 8192), ("inkling2-mini-100k", 100096), ("inkling2-mini-long", 150016)])
        self.assertEqual(self.lifecycle.calls, [])
        controller.apply_snapshot(([], {"state": "running", "running": True, "active_model": "inkling2-mini-100k", "context": 100096}))
        console = self.console()
        console.print(render_dashboard(controller.state, 115, 32))
        displayed = console.file.getvalue()
        self.assertIn("Loaded inkling2-mini-100k", displayed)
        self.assertIn("Experimental floor: 512 MiB VRAM", displayed)
        self.assertIn("mixed test min 1,343 MiB", evidence_lines(controller.state.selected)[0])
        controller.choose_profile_context("inkling2-mini-100k", 100096)
        controller.state.move(1)
        controller.state.move(-1)
        self.assertEqual(controller.state.selected["id"], "inkling2-mini-100k")

    def test_vision_quick_delegates_exact_profile_only_with_native_image_projector(self):
        controller = self.controller
        controller.state.connected = True
        vision = {**self.rows[1], "context": 150016, "input": ["text", "image"], "mmproj": str(self.root / "projector.gguf")}
        controller.benchmark_selected("vision-quick", vision)
        self.assertEqual(self.client.calls, [("inkling2-mini-long", 150016, "vision-quick")])
        for row in (self.rows[0], {**vision, "mmproj": ""}, {**vision, "input": ["text"]}, {**vision, "id": "saluki27b"}, {**vision, "id": "inkling2-mini-100k"}):
            with self.subTest(row=row["id"]), self.assertRaises(ValueError):
                controller.benchmark_selected("vision-quick", row)
        with self.assertRaisesRegex(ValueError, "Near-context benchmark"):
            controller.benchmark_selected("near-context", vision)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.lifecycle.calls, [])

    def test_context_options_are_exact_preexisting_profiles(self):
        options = profile_context_options(self.controller.state.selected_variants)
        self.assertEqual(options, [("inkling2-mini", 4096), ("inkling2-mini", 8192), ("inkling2-mini-long", 100096)])
        self.assertEqual(self.controller.state.selected["id"], "inkling2-mini")
        self.assertEqual(self.controller.state.selected["context"], 8192)

    def test_unified_context_menu_selects_long_alias_without_widening_any_range(self):
        console = self.console()
        with patch.object(console, "input", return_value="3"):
            self.assertEqual(choose_context_profile(console, self.controller.state.selected_variants, "inkling2-mini"), ("inkling2-mini-long", 100096))
        self.assertIn("opt-in", console.file.getvalue())
        self.assertEqual(self.rows[0]["maximum_context"], 8192)
        self.assertEqual(self.rows[1]["minimum_context"], 100096)

    def test_custom_context_stays_inside_chosen_profile(self):
        console = self.console()
        variants = self.controller.state.selected_variants
        with patch.object(console, "input", side_effect=["c", "1", "6144"]):
            self.assertEqual(choose_context_profile(console, variants), ("inkling2-mini", 6144))
        for inputs in (["c", "1", "16384"], ["c", "2", "32768"], ["c", "2", "131072"]):
            with self.subTest(inputs=inputs), patch.object(console, "input", side_effect=inputs), self.assertRaises(ValueError):
                choose_context_profile(console, variants)

    def test_start_swap_and_benchmark_dispatch_exact_selected_alias_and_context(self):
        before = self.registry.read_bytes()
        self.controller.choose_profile_context("inkling2-mini-long", 100096)
        self.controller.start_selected()
        self.assertEqual(self.lifecycle.calls[-1], {"model": "inkling2-mini-long", "context": 100096, "direct": False})
        self.assertEqual(self.controller.state.selected["engine"], self.rows[1]["engine"])
        self.controller.state.connected = True
        self.controller.benchmark_selected("quick")
        self.assertEqual(self.client.calls[-1], ("inkling2-mini-long", 100096, "quick"))
        self.controller.choose_profile_context("inkling2-mini", 4096)
        self.controller.start_selected()
        self.assertEqual(self.lifecycle.calls[-1], {"model": "inkling2-mini", "context": 4096, "direct": False})
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.rows[0]["context_presets"], [4096, 8192])

    def test_long_variant_selection_survives_navigation_to_other_model(self):
        self.controller.choose_profile_context("inkling2-mini-long", 100096)
        self.controller.state.move(1)
        self.assertEqual(self.controller.state.selected["id"], "saluki27b")
        self.controller.state.move(-1)
        self.assertEqual(self.controller.state.selected["id"], "inkling2-mini-long")
        self.assertEqual(len(self.controller.state.visible_models), 2)

    def test_search_matches_variant_alias_without_silently_selecting_long_mode(self):
        self.controller.state.filter_text = "100K"
        self.assertEqual(len(self.controller.state.visible_models), 1)
        self.assertEqual(self.controller.state.selected["id"], "inkling2-mini")
        self.controller.state.filter_text = "inkling2-mini-long"
        self.assertEqual(len(self.controller.state.visible_models), 1)
        self.controller.state.filter_text = "saluki"
        self.assertEqual(self.controller.state.selected["id"], "saluki27b")

    def test_selected_vs_loaded_alias_and_estimates_are_not_conflated(self):
        self.controller.state.status = {"state": "running", "active_model": "inkling2-mini-long", "context": 100096}
        self.controller.state.connected = True
        console = self.console()
        console.print(render_dashboard(self.controller.state, 115, 30, 10.1))
        output = console.file.getvalue()
        self.assertEqual(len(self.controller.state.visible_models), 2)
        self.assertEqual(sum(row.get("name") == "Inkling · shared artifact" for row in self.controller.state.visible_models), 1)
        self.assertEqual(output.count("● LOADED"), 1)
        self.assertIn("Selected inkling2-mini", output)
        self.assertIn("Loaded inkling2-mini-long", output)
        self.assertIn("100,096", output)
        self.assertIn("12.11 GiB", output)
        self.assertIn("LOADED", output)
        self.controller.choose_profile_context("inkling2-mini-long", 100096)
        self.assertIn("14.16 GiB", evidence_lines(self.controller.state.selected)[0])

    def test_short_measurements_do_not_attach_to_long_profile(self):
        measured = {"source": "measured", "model": "inkling2-mini", "context": 8192, "resources": {"minimum_free_vram_mib": 2500, "minimum_available_ram_gib": 3}}
        self.assertIn("MEASURED minimum free", evidence_lines(self.controller.state.selected, measured)[1])
        self.controller.choose_profile_context("inkling2-mini-long", 100096)
        self.assertIn("unavailable", evidence_lines(self.controller.state.selected, measured)[1])
        self.assertNotEqual(self.controller.state.selected["engine"], self.rows[0]["engine"])

    def test_grouped_compact_render_keeps_flow_and_controls_visible(self):
        from rich.console import Console
        state = self.controller.state
        state.connected = True
        state.status = {"state": "running", "active_model": "inkling2-mini-long", "context": 100096, "request_flow": RequestFlowTests().state().status["request_flow"]}
        state.flow_sampled_at = 10
        for width, height in ((115, 30), (80, 24)):
            with self.subTest(width=width, height=height):
                output = io.StringIO()
                Console(file=output, width=width, height=height, color_system=None).print(render_dashboard(state, width, height, 10.1))
                self.assertLessEqual(len(output.getvalue().splitlines()), height)
                self.assertIn("Stop STACK", output.getvalue())
                self.assertIn("Q Detach", output.getvalue())


if __name__ == "__main__":
    unittest.main()
