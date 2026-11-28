"""CPU-only tests of trusted profile reserve selection and ownership guards."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_stack.registry import StackError, load_registry, resource_limits
from local_stack.runtime import ModelRuntime


EXPERIMENTAL = {"minimum_free_vram_mib": 512, "minimum_available_ram_gib": 1.25}
GLOBAL = {"minimum_free_vram_mib": 2048, "minimum_available_ram_gib": 2,
          "startup_minimum_available_ram_gib": 3, "maximum_startup_gpu_utilization_percent": 92}


def resources(vram, ram, gpu=0):
    return {"free_vram_mib": vram, "available_ram_gib": ram, "gpu_utilization_percent": gpu}


class ProfileResourceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = self.root / "config" / "models.json"
        self.config.parent.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.engine = self.root / "llama-server.exe"
        self.engine.touch()
        self.saluki = self.model("saluki27b")
        self.experimental = {**self.model("inkling2-mini-long"), "experimental_resources": dict(EXPERIMENTAL)}
        self.write_registry([self.saluki, self.experimental])

    def model(self, alias):
        artifact = self.root / f"{alias}.gguf"
        artifact.write_bytes(b"GGUFfixture")
        return {"id": alias, "name": alias, "path": str(artifact), "engine": str(self.engine), "context": 8192}

    def write_registry(self, models, *, limits=GLOBAL):
        self.config.write_text(json.dumps({"schema_version": 1, "models": models, "resources": limits}), encoding="utf-8")

    def make_runtime(self, samples=None):
        measure = Mock(return_value=resources(8192, 8), side_effect=samples)
        child = Mock(pid=42)
        child.poll.return_value = None
        identity = {"executable": str(self.engine), "created": "1"}
        runtime = ModelRuntime(self.config, self.state, resources=measure, spawn=Mock(return_value=child),
                               identify=Mock(return_value=identity), terminate=Mock())
        runtime._listener_present = Mock(return_value=False)
        runtime._check_health = Mock()
        runtime._backend_idle = Mock()
        runtime.sleep = Mock()
        return runtime, identity

    def test_registry_only_exact_keys_and_typed_bounded_values(self):
        self.assertEqual(load_registry(self.config)["models"][1]["experimental_resources"], EXPERIMENTAL)
        self.assertNotIn("experimental_resources", load_registry(self.config)["models"][0])
        for bad in (
            [], None, {}, {"minimum_free_vram_mib": 512},
            {**EXPERIMENTAL, "startup_minimum_available_ram_gib": 1.25},
            {**EXPERIMENTAL, "minimum_free_vram_mib": 511},
            {**EXPERIMENTAL, "minimum_free_vram_mib": 512.0},
            {**EXPERIMENTAL, "minimum_free_vram_mib": True},
            {**EXPERIMENTAL, "minimum_free_vram_mib": 2049},
            {**EXPERIMENTAL, "minimum_available_ram_gib": 1.24999},
            {**EXPERIMENTAL, "minimum_available_ram_gib": 2.01},
            {**EXPERIMENTAL, "minimum_available_ram_gib": "1.25"},
            {**EXPERIMENTAL, "minimum_available_ram_gib": float("nan")},
            {**EXPERIMENTAL, "minimum_available_ram_gib": float("inf")},
            {**EXPERIMENTAL, "minimum_available_ram_gib": True},
        ):
            with self.subTest(bad=bad):
                self.write_registry([self.saluki, {**self.experimental, "experimental_resources": bad}])
                with self.assertRaisesRegex(StackError, "experimental_resources"):
                    load_registry(self.config)

    def test_shared_resolver_uses_detached_selected_limits(self):
        selected = load_registry(self.config)["models"][1]
        self.assertEqual(resource_limits(GLOBAL, selected), {**EXPERIMENTAL, "experimental": True})
        self.assertEqual(resource_limits(GLOBAL, selected, startup=True),
                         {**EXPERIMENTAL, "minimum_available_ram_gib": 3, "experimental": True})
        self.assertEqual(resource_limits({}, self.saluki),
                         {"minimum_free_vram_mib": 2048, "minimum_available_ram_gib": 2, "experimental": False})
        result = resource_limits(GLOBAL, selected)
        result["minimum_free_vram_mib"] = 0
        self.assertEqual(selected["experimental_resources"], EXPERIMENTAL)
        self.assertEqual(GLOBAL["minimum_free_vram_mib"], 2048)

    def test_no_weakening_through_registration_or_persisted_overlay(self):
        runtime, _ = self.make_runtime()
        attempted = {**self.model("other"), "experimental_resources": EXPERIMENTAL}
        with self.assertRaises(StackError) as failure:
            runtime.register(attempted)
        self.assertEqual(failure.exception.status, 403)
        self.assertEqual(len(runtime.all_models()), 2)
        self.assertFalse((self.state / "overrides.json").exists())
        (self.state / "overrides.json").write_text(json.dumps({"models": {}, "registered": [attempted]}))
        with self.assertRaisesRegex(StackError, "experimental_resources"):
            self.make_runtime()

    def test_experimental_startup_estimate_ram_and_gpu_busy_do_not_bypass_preflight(self):
        self.experimental["estimated_vram_mib"] = 1024
        self.write_registry([self.saluki, self.experimental])
        for sample, error_text in (
            (resources(1535, 3), "1536 MiB free VRAM"),
            (resources(1536, 2.99), "3 GiB available RAM"),
            (resources(1536, 3, 93), "GPU is busy"),
        ):
            with self.subTest(sample=sample):
                runtime, _ = self.make_runtime([sample])
                with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "fixture"}), self.assertRaisesRegex(StackError, error_text):
                    runtime.load("inkling2-mini-long")
                runtime.spawn.assert_not_called()

    def test_experimental_load_readiness_and_reuse_while_global_reserves_unchanged(self):
        self.experimental["estimated_vram_mib"] = 1024
        self.write_registry([self.saluki, self.experimental])
        runtime, _ = self.make_runtime([resources(1536, 3), resources(600, 1.5), resources(512, 1.25),
                                        resources(512, 1.25), resources(511, 1.25), resources(512, 1.249),
                                        resources(2047, 3)])
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "fixture"}):
            self.assertEqual(runtime.load("inkling2-mini-long")["context"], 8192)
            self.assertEqual(runtime.load("inkling2-mini-long")["active_model"], "inkling2-mini-long")
            for _ in range(2):
                with self.assertRaisesRegex(StackError, "need 1.25 GiB available RAM and 512 MiB"):
                    runtime.load("inkling2-mini-long")
            with self.assertRaisesRegex(StackError, "2048 MiB free VRAM"):
                runtime._start(runtime.find_model("saluki27b"), 8192)
        runtime.spawn.assert_called_once()
        self.assertEqual(runtime.base_registry["resources"], GLOBAL)
        self.assertNotIn("experimental_resources", runtime.find_model("saluki27b"))

    def test_readiness_rechecks_reserve_after_health(self):
        runtime, _ = self.make_runtime([resources(600, 3), resources(512, 1.25), resources(511, 1.25)])
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "fixture"}), self.assertRaisesRegex(StackError, "512 MiB"):
            runtime.load("inkling2-mini-long")
        runtime._check_health.assert_called_once()
        runtime.terminate.assert_called_once()
        self.assertIsNone(runtime.record)

    def test_failed_normal_switch_restores_original_experimental_policy(self):
        # Startup of the non-experimental target must fail at 600 MiB; rollback of
        # the old profile must use that old profile's own lower steady reserve.
        runtime, identity = self.make_runtime([resources(600, 3.5), resources(600, 3.5),
                                               resources(600, 1.5), resources(512, 1.25)])
        old = runtime.find_model("inkling2-mini-long")
        runtime.record = {"pid": 41, "identity": identity, "model": old["id"], "context": 8192,
                          "profile": old, "ready": True}
        with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "fixture"}), self.assertRaisesRegex(StackError, "2048 MiB"):
            runtime.load("saluki27b")
        self.assertEqual(runtime.record["model"], old["id"])
        self.assertTrue(runtime.record["ready"])
        self.assertNotIn("could not be restored automatically", runtime.last_error)
        runtime.spawn.assert_called_once()
        self.assertIn(old["id"], runtime.spawn.call_args.args[0])

    def test_untrusted_rollback_marker_cannot_introduce_policy_for_saluki(self):
        runtime, _ = self.make_runtime([resources(600, 3.5), resources(600, 3.5)])
        untrusted = {**runtime.find_model("saluki27b"), "experimental_resources": EXPERIMENTAL}
        for model in (untrusted, {**untrusted, "id": "inkling2-mini-long"}):
            with patch.dict(os.environ, {"LLAMA_CPP_API_KEY": "fixture"}), self.assertRaisesRegex(StackError, "2048 MiB"):
                runtime._start(model, 8192)
        runtime.spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
