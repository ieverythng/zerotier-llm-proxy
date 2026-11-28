"""Offline benchmark interface tests: no real network, GPU, model, or process."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from local_stack import benchmark
from local_stack.registry import StackError


def _event(data):
    return b"data: " + json.dumps(data).encode() + b"\n\n"


class Reply(io.BytesIO):
    def __init__(self, data, status=200, stalling=None):
        super().__init__(data)
        self.status = status
        self.stalling = stalling

    def _wait_if_stalled(self):
        if self.stalling is not None:
            if not self.stalling.wait(2):
                raise AssertionError("Expected the watchdog to close the stalled request")
            raise OSError("synthetic socket shutdown")

    def read(self, limit):
        self._wait_if_stalled()
        return super().read(limit)

    def readline(self, limit):
        self._wait_if_stalled()
        return super().readline(limit)


class NativeFixture:
    def __init__(self):
        self.calls = []
        self.override = None
        self.timings_override = {}
        self.skip_final = False
        self.blocked_path = None
        self.started = threading.Event()
        self.aborted = threading.Event()
        self.answer = " COPPER LANTERN\n"
        self.quality_stop = "stop"
        self.chat_answers = None
        self.usage = {"prompt_tokens": 31, "completion_tokens": 87, "total_tokens": 118}

    def connect(self, port):
        fixture = self

        class Connection:
            def request(self, method, path, *, body, headers):
                self.path, self.payload = path, json.loads(body)
                fixture.calls.append((port, method, path, self.payload, headers))

            def getresponse(self):
                if fixture.override is not None:
                    return fixture.override
                if self.path == fixture.blocked_path:
                    fixture.started.set()
                    return Reply(b"", stalling=fixture.aborted)
                if self.path == "/tokenize":
                    return Reply(json.dumps({"tokens": list(range(11, 8203))}).encode())
                if self.path == "/v1/chat/completions":
                    answer, finish = ((fixture.answer, fixture.quality_stop) if fixture.chat_answers is None
                                      else fixture.chat_answers.pop(0))
                    return Reply(json.dumps({"usage": fixture.usage, "choices": [{
                        "message": {"role": "assistant", "content": answer, "reasoning_content": "synthetic reasoning"},
                        "finish_reason": finish,
                    }]}).encode())
                if self.path != "/completion":
                    raise AssertionError("Unexpected endpoint")
                timings = {"prompt_n": len(self.payload["prompt"]), "cache_n": 0, "predicted_n": self.payload["n_predict"],
                           "prompt_ms": 23, "prompt_per_second": 45, "predicted_ms": 67, "predicted_per_second": 89}
                timings.update(fixture.timings_override)
                lines = [b": synthetic heartbeat\n", _event({"content": "synthetic output"})]
                if not fixture.skip_final:
                    lines.append(_event({"stop": True, "stop_type": "limit", "timings": timings}))
                return Reply(b"".join(lines))

            def close(self):
                pass

            def abort(self):
                fixture.aborted.set()

        return Connection()


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        artifact, engine = root / "fictional.gguf", root / "llama-server.exe"
        artifact.write_bytes(b"GGUF fake fixture")
        engine.write_bytes(b"not an executable")
        self.model = {"id": "fictional-model", "path": str(artifact), "engine": str(engine),
                      "context": 8192, "minimum_context": 512, "maximum_context": 32768,
                      "max_output_tokens": 256, "estimated_vram_mib": 4321, "kv_cache": "q4_0"}
        self.runtime = Mock(backend_port=19751, state_dir=root / "state", base_registry={})
        self.runtime.find_model.return_value = self.model
        self.runtime.ensure_loaded.return_value = self.model
        self.runtime.resources.return_value = {"available_ram_gib": 5.25, "free_vram_mib": 5500}
        self.native = NativeFixture()
        self.power = Mock()
        for patcher in (patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "synthetic-secret"}),
                        patch.object(benchmark, "_require_ac", self.power),
                        patch.object(benchmark, "_LoopbackConnection", self.native.connect)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_benchmark(self, **params):
        return benchmark.run_benchmark(self.runtime, self.model["id"], **params)

    def preview(self, context=None):
        return benchmark.benchmark_preview(self.model, context, state_dir=self.runtime.state_dir)

    def test_quick_reports_exact_native_metrics_and_safe_persisted_summary(self):
        result = self.run_benchmark()
        self.assertEqual((result["status"], result["source"], result["profile"]), ("ok", "measured", "quick"))
        self.runtime.ensure_loaded.assert_called_once_with(self.model["id"], 8192)
        self.runtime.acquire.assert_not_called()
        self.runtime.release.assert_not_called()
        self.assertEqual([call[2] for call in self.native.calls], ["/tokenize", "/completion", "/v1/chat/completions"])
        for port, method, _, _, headers in self.native.calls:
            self.assertEqual((port, method, headers["Authorization"]), (19751, "POST", "Bearer synthetic-secret"))
        perf, quality = self.native.calls[1][3], self.native.calls[2][3]
        self.assertEqual(perf["prompt"], list(range(11, 1035)))
        self.assertEqual(quality["model"], self.model["id"])
        self.assertEqual([message["role"] for message in quality["messages"]], ["user"])
        self.assertIn("COPPER LANTERN", quality["messages"][0]["content"])
        self.assertNotIn("stop", quality)
        self.assertFalse(quality["store"])
        self.assertTrue(perf["ignore_eos"])
        self.assertFalse(quality["ignore_eos"])
        self.assertFalse(perf["cache_prompt"])
        self.assertFalse(quality["cache_prompt"])
        self.assertEqual((perf["n_predict"], quality["max_tokens"]), (64, 256))
        self.assertEqual(result["quality"], {"passed": True, "prompt_tokens": 31, "output_tokens": 87})
        performance = result["performance"]
        self.assertEqual((performance["prompt_tokens"], performance["output_tokens"]), (1024, 64))
        self.assertEqual([performance[k] for k in ("prefill_ms", "prefill_tps", "decode_ms", "decode_tps")], [23, 45, 67, 89])
        self.assertGreaterEqual(performance["wall_ms"], performance["ttft_ms"])
        self.assertEqual(result["resources"]["minimum_free_vram_mib"], 5500)
        self.assertGreaterEqual(result["resources"]["sample_count"], 2)
        self.assertTrue(result["persisted"])
        (summary,) = (self.runtime.state_dir / "benchmarks").glob("*.json")
        saved = summary.read_text(encoding="utf-8")
        self.assertEqual(json.loads(saved)["result"], result)
        for forbidden in ("synthetic-secret", "synthetic output", "synthetic reasoning", "COPPER LANTERN", "fictional.gguf", "llama-server.exe"):
            self.assertNotIn(forbidden, saved)

    def test_near_context_is_bounded_including_context_rounding(self):
        result = self.run_benchmark(profile="near-context", context=8150)
        self.assertEqual((result["context"], result["performance"]["prompt_tokens"], result["performance"]["output_tokens"]),
                         (8192, 7168, 128))
        result = self.run_benchmark(profile="near-context", context=4096)
        self.assertEqual(result["performance"]["prompt_tokens"], 3584)

    def test_bad_profile_or_context_rejected_before_loading(self):
        for params in ({"profile": "arbitrary"}, {"profile": "near-context", "context": 8193},
                       {"context": "8192"}, {"context": 33000}):
            with self.subTest(params=params), self.assertRaises(StackError):
                self.run_benchmark(**params)
        self.runtime.ensure_loaded.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def prepare_vision_fixture(self):
        projector = Path(self.model["path"]).with_name("fictional-projector.gguf")
        projector.write_bytes(b"GGUF fake projector")
        self.model.update(id="inkling2-mini-long", input=["text", "image"], mmproj=str(projector), mmproj_gpu=False)
        for name, value in (("_INKLING_VISION_ARTIFACT", Path(self.model["path"])),
                            ("_INKLING_VISION_PROJECTOR", projector)):
            patcher = patch.object(benchmark, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.native.chat_answers = [(text, "stop") for text in (
            "LEFT RED", "RIGHT BLUE", "4073", "391.0", '{"item":"meridian","units":7}',
            "left = mid + 1", '{"revision":3,"status":"approved"}',
        )]

    def test_vision_quick_runs_fixed_pixel_and_text_cases_and_saves_only_scores(self):
        self.prepare_vision_fixture()
        result = self.run_benchmark(profile="vision-quick")
        self.assertEqual((result["performance"]["prompt_tokens"], result["performance"]["output_tokens"]), (1024, 64))
        quality = result["quality"]
        self.assertTrue(quality["passed"])
        self.assertEqual(quality["passed_count"], quality["case_count"])
        self.assertEqual([case["modality"] for case in quality["cases"]], ["image"] * 3 + ["text"] * 4)
        self.assertEqual(len({case["case"] for case in quality["cases"]}), 7)
        for case in quality["cases"]:
            self.assertEqual(set(case), {"case", "modality", "passed", "finish", "prompt_tokens", "output_tokens", "wall_ms"})
            self.assertEqual((case["finish"], case["prompt_tokens"], case["output_tokens"]), ("stop", 31, 87))
            self.assertGreaterEqual(case["wall_ms"], 0)
        chats = [call[3] for call in self.native.calls if call[2] == "/v1/chat/completions"]
        images = [chat["messages"][0]["content"] for chat in chats[:3]]
        self.assertEqual(images[0][1]["text"], images[1][1]["text"])
        self.assertEqual(len({parts[0]["image_url"]["url"] for parts in images}), 3)
        self.assertTrue(all(parts[0]["image_url"]["url"].startswith("data:image/png;base64,") for parts in images))
        self.assertTrue(all(isinstance(chat["messages"][0]["content"], str) for chat in chats[3:]))
        for chat in chats:
            self.assertEqual(chat["model"], "inkling2-mini-long")
            self.assertNotIn("tools", chat)
            self.assertFalse(chat["store"])
            self.assertFalse(chat["cache_prompt"])
            self.assertFalse(chat["ignore_eos"])
            self.assertLessEqual(chat["max_tokens"], 256)
        self.runtime.ensure_loaded.assert_called_once_with(self.model["id"], 8192)
        self.runtime.acquire.assert_not_called()
        self.runtime.release.assert_not_called()
        (summary,) = (self.runtime.state_dir / "benchmarks").glob("*.json")
        saved = summary.read_text(encoding="utf8")
        self.assertEqual(json.loads(saved)["result"], result)
        for forbidden in ("data:image", "4073", "LEFT RED", "RIGHT BLUE", "meridian", "mid + 1", "synthetic reasoning", "synthetic-secret"):
            self.assertNotIn(forbidden, saved)

    def test_vision_quick_fails_individual_bad_scores_without_saving_answers(self):
        self.prepare_vision_fixture()
        self.native.chat_answers[0] = ("LEFT RED", "length")
        self.native.chat_answers[3] = ("391.0 plus commentary", "stop")
        self.native.chat_answers[4] = ('{"item":"bad","item":"meridian","units":7}', "stop")
        self.native.chat_answers[6] = ('{"revision":true,"status":"approved"}', "untrusted finish text")
        result = self.run_benchmark(profile="vision-quick")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["quality"]["passed"])
        self.assertEqual(result["quality"]["passed_count"], 3)
        self.assertEqual(result["quality"]["cases"][-1]["finish"], "unknown")
        self.assertNotIn("untrusted finish text", json.dumps(result))

    def test_150k_vision_benchmark_inherits_only_selected_experimental_reserve(self):
        self.prepare_vision_fixture()
        self.model.update(context=150016, minimum_context=150016, maximum_context=150016, max_output_tokens=10000,
                          experimental_resources={"minimum_free_vram_mib": 512, "minimum_available_ram_gib": 1.25})
        self.runtime.base_registry = {"resources": {"minimum_free_vram_mib": 2048, "minimum_available_ram_gib": 2}}
        self.runtime.resources.return_value = {"available_ram_gib": 1.25, "free_vram_mib": 512}
        result = self.run_benchmark(profile="vision-quick")
        self.assertEqual(result["context"], 150016)
        self.assertEqual(result["resources"]["reserve"], {"source": "experimental", "ram_floor_gib": 1.25, "vram_floor_mib": 512})
        self.assertEqual(self.preview()["source"], "measured")
        self.assertIn("experimental reserve", self.preview()["note"])
        self.model["mmproj_gpu"] = True
        self.assertEqual(self.preview()["source"], "configured")
        self.model["mmproj_gpu"] = False
        self.assertEqual(self.preview()["source"], "measured")
        self.runtime.resources.return_value["free_vram_mib"] = 511
        with self.assertRaisesRegex(StackError, "selected experimental reserve"):
            self.run_benchmark(profile="vision-quick")
        self.model.pop("experimental_resources")
        self.assertEqual(self.preview()["source"], "configured")
        self.runtime.resources.return_value["free_vram_mib"] = 512
        with self.assertRaisesRegex(StackError, "production reserve"):
            self.run_benchmark(profile="vision-quick")
        self.assertEqual(len(self.native.calls), 9)

    def test_vision_quick_refuses_wrong_alias_artifact_or_capability_before_load(self):
        self.prepare_vision_fixture()
        for change in ({"id": "saluki27b"}, {"path": self.model["engine"]},
                       {"input": ["text"]}, {"mmproj": None}, {"mmproj": self.model["path"]}):
            with self.subTest(change=change), patch.dict(self.model, change):
                with self.assertRaisesRegex(StackError, "registered Inkling"):
                    self.run_benchmark(profile="vision-quick")
        self.runtime.ensure_loaded.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def test_battery_refusal_precedes_model_loading(self):
        self.power.side_effect = StackError("AC power is required", 503)
        with self.assertRaisesRegex(StackError, "AC power"):
            self.run_benchmark()
        self.runtime.ensure_loaded.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def test_missing_auth_precedes_model_loading(self):
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": ""}), self.assertRaisesRegex(StackError, "authenticated loopback"):
            self.run_benchmark()
        self.runtime.ensure_loaded.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def test_stricter_configured_floors_and_production_minima_are_enforced(self):
        self.runtime.base_registry = {"resources": {"minimum_free_vram_mib": 6000}}
        with self.assertRaisesRegex(StackError, "production reserve"):
            self.run_benchmark()
        self.runtime.base_registry = {"resources": {"minimum_available_ram_gib": 1, "minimum_free_vram_mib": 1024}}
        self.runtime.resources.return_value = {"available_ram_gib": 2.5, "free_vram_mib": 2047}
        with self.assertRaisesRegex(StackError, "production reserve"):
            self.run_benchmark()
        self.assertEqual(self.native.calls, [])

    def test_reserve_watchdog_cancels_blocked_native_read(self):
        self.native.blocked_path = "/completion"

        def sample():
            if self.native.started.is_set():
                return {"available_ram_gib": 1.5, "free_vram_mib": 5500}
            return {"available_ram_gib": 5.25, "free_vram_mib": 5500}

        self.runtime.resources.side_effect = sample
        with patch.object(benchmark, "_SAMPLE_SECONDS", 0.01), self.assertRaisesRegex(StackError, "production reserve"):
            self.run_benchmark()
        self.assertTrue(self.native.aborted.is_set())
        self.runtime.terminate.assert_not_called()
        self.assertEqual(self.preview()["source"], "configured")

    def test_deadline_cancels_stalled_stream(self):
        self.native.blocked_path = "/completion"
        with patch.object(benchmark, "_DEADLINE_SECONDS", 0.05), self.assertRaises(StackError) as error:
            self.run_benchmark()
        self.assertEqual(error.exception.status, 504)
        self.assertTrue(self.native.aborted.is_set())

    def test_native_redirect_is_not_followed(self):
        self.native.override = Reply(b"", status=302)
        with self.assertRaisesRegex(StackError, "HTTP 302"):
            self.run_benchmark()
        self.assertEqual([call[2] for call in self.native.calls], ["/tokenize"])

    def test_incomplete_wrong_cached_and_nonfinite_measurements_are_not_saved(self):
        for timings in ({"prompt_n": 1023}, {"cache_n": 1}, {"predicted_n": 2}, {"prompt_ms": float("nan")}):
            self.native.timings_override = timings
            with self.subTest(timings=timings), self.assertRaises(StackError):
                self.run_benchmark()
        self.native.timings_override = {}
        self.native.skip_final = True
        with self.assertRaisesRegex(StackError, "final timing"):
            self.run_benchmark()
        self.assertEqual(self.preview()["source"], "configured")

    def test_quality_canary_failure_does_not_invalidate_performance(self):
        self.native.answer = "wrong label"
        result = self.run_benchmark()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["quality"]["passed"])
        self.native.answer = "COPPER LANTERN"
        for reason in ("length", "tool_calls", None):
            self.native.quality_stop = reason
            with self.subTest(reason=reason):
                self.assertFalse(self.run_benchmark()["quality"]["passed"])

    def test_chat_usage_requires_real_counts_and_context_headroom(self):
        for usage in (None, {"prompt_tokens": 31, "completion_tokens": 257, "total_tokens": 288},
                      {"prompt_tokens": 31, "completion_tokens": 87, "total_tokens": 87},
                      {"prompt_tokens": 241, "completion_tokens": 87, "total_tokens": 328}):
            self.native.usage = usage
            with self.subTest(usage=usage), self.assertRaisesRegex(StackError, "usage"):
                self.run_benchmark(context=512)
        self.assertEqual(self.preview(512)["source"], "configured")

    def test_chat_canary_respects_smaller_model_output_cap(self):
        self.model["max_output_tokens"] = 96
        result = self.run_benchmark()
        self.assertTrue(result["quality"]["passed"])
        self.assertEqual(self.native.calls[2][3]["max_tokens"], 96)

    def test_deadline_cancels_stalled_chat_quality_read(self):
        self.native.blocked_path = "/v1/chat/completions"
        with patch.object(benchmark, "_DEADLINE_SECONDS", 0.1), self.assertRaises(StackError) as error:
            self.run_benchmark()
        self.assertEqual(error.exception.status, 504)
        self.assertTrue(self.native.started.is_set())
        self.assertTrue(self.native.aborted.is_set())
        self.assertEqual(self.preview()["source"], "configured")

    def test_preview_separates_config_estimate_and_exact_matching_observation(self):
        self.assertEqual(self.preview()["source"], "configured")
        self.assertEqual(self.preview()["estimated_vram_mib"], 4321)
        self.assertIsNone(self.preview()["measured_minimum_available_ram_gib"])
        self.run_benchmark()
        self.assertEqual(self.preview()["source"], "measured")
        self.assertEqual(self.preview()["measured_minimum_available_ram_gib"], 5.25)
        self.assertEqual(self.preview(4096)["source"], "configured")
        self.model["kv_cache"] = "f16"
        self.assertEqual(self.preview()["source"], "configured")
        self.model["kv_cache"] = "q4_0"
        Path(self.model["engine"]).write_bytes(b"new fake executable fingerprint")
        self.assertEqual(self.preview()["source"], "configured")
        self.model["estimated_vram_mib"] = 0
        self.assertEqual(self.preview()["source"], "unavailable")

    def test_persistence_error_is_a_measured_but_unsaved_run(self):
        with patch.object(benchmark, "atomic_json", side_effect=OSError("secret local path")):
            result = self.run_benchmark()
        self.assertFalse(result["persisted"])
        self.assertEqual(result["source"], "measured")
        self.assertNotIn("secret local path", json.dumps(result))
        self.assertEqual(self.preview()["source"], "configured")


if __name__ == "__main__":
    unittest.main()
