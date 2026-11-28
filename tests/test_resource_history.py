"""Pure system-history tests: no OS collection, subprocess or live endpoint."""
import io
import unittest
from datetime import datetime, timedelta, timezone

from local_stack.resource_history import ResourceHistory, bar_rows, normalize_sample, plot_rows, render_resource_history, sparkline


BASE = datetime(2026, 11, 24, 3, 0, 0, tzinfo=timezone.utc)


def observation(seconds=0, **resources):
    values = {"cpu_utilization_percent": 25, "gpu_utilization_percent": 50, "total_ram_gib": 16, "available_ram_gib": 4, "total_vram_mib": 16384, "free_vram_mib": 4096, "cpu_interval_seconds": 2}
    values.update(resources)
    return {"resources_stale": False, "resources_sampled_at": (BASE + timedelta(seconds=seconds)).isoformat(), "resources": values}


def stage(name, sequence, *, request_id="req-1", source="observed"):
    return {"id": request_id, "stage": name, "event_seq": sequence, "source": source, "outcome": name}


def gateway(seconds, *, resource_seconds=None, generation="a", flow_generation=None, active=(), last=None):
    status = observation(seconds if resource_seconds is None else resource_seconds)
    status["supervisor_generation"] = generation
    status["request_flow"] = {"version": 1, "generation": flow_generation or generation,
                              "observed_at": observation(seconds)["resources_sampled_at"], "active": list(active), "last": last}
    return status


class HistoryZoomTests(unittest.TestCase):
    def test_zoom_is_a_view_over_the_same_bounded_samples(self):
        history = ResourceHistory(max_points=4)
        for t in (0, 120, 240, 360, 480):
            history.observe(observation(t), connected=True, now=BASE + timedelta(seconds=t))
        end = BASE + timedelta(seconds=480)
        before = tuple(history.samples)
        self.assertEqual(len(before), 4)
        self.assertEqual(sum(v is not None for v in history.series('cpu', 80, end)), 2)
        for expected_span, expected_samples in ((300, 3), (900, 4), (120, 2)):
            self.assertEqual(history.zoom(), expected_span)
            self.assertEqual(tuple(history.samples), before)
            self.assertEqual(sum(v is not None for v in history.series('cpu', 80, end)), expected_samples)


class GatewayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = ResourceHistory(window_seconds=12)

    def observe(self, seconds, *, received=None, **updates):
        self.history.observe(gateway(seconds, **updates), connected=True,
                             now=BASE + timedelta(seconds=seconds if received is None else received))

    def test_marks_use_gateway_observed_time_and_resource_uses_own_time(self):
        self.observe(2, resource_seconds=0, active=[stage("forward_wait", 3)])
        self.observe(4, resource_seconds=0, active=[stage("streaming", 5)])
        self.observe(6, resource_seconds=0, last=stage("completed", 6))
        self.observe(8, resource_seconds=2, last=stage("completed", 6))
        end = BASE + timedelta(seconds=12)
        self.assertEqual(self.history.gateway_lane(12, end), "  w s +     ")
        self.assertEqual(self.history.series("gpu", 12, end), (50, None, 50) + (None,) * 9)
        self.assertEqual([mark.stage for mark in self.history.gateway_marks], ["forward_wait", "streaming", "completed"])

    def test_attach_and_resume_do_not_show_retained_terminal_snapshot_as_new_finish(self):
        self.observe(0, last=stage("completed", 4))
        self.observe(2, last=stage("completed", 4), active=[stage("streaming", 2, request_id="req-2")])
        self.history.observe({}, connected=False, now=BASE + timedelta(seconds=3))
        self.observe(4, last=stage("failed", 3, request_id="req-2"))
        self.observe(6, last=stage("failed", 3, request_id="req-2"))
        self.observe(8, last=stage("rejected", 2, request_id="req-3"))
        self.assertEqual([mark.stage for mark in self.history.gateway_marks], ["streaming", "rejected"])

    def test_rejects_old_future_inferred_bad_sequence_and_wrong_generation_marks(self):
        self.observe(0, active=[stage("forward_wait", 3)])
        self.observe(2, received=9, active=[stage("streaming", 4)])
        self.observe(10, received=9, active=[stage("streaming", 4)])
        self.observe(11, flow_generation="unknown", active=[stage("streaming", 4)])
        self.observe(12, active=[stage("streaming", 4, source="inferred"), stage("streaming", 2),
                                 stage("streaming", 4, request_id="untrusted-id"), stage(["invalid"], 4)])
        self.observe(11, received=12, active=[stage("streaming", 4)])
        self.assertEqual([mark.stage for mark in self.history.gateway_marks], ["forward_wait"])
        self.assertIsNone(self.history.latest(BASE + timedelta(seconds=12)))

    def test_generation_reset_and_late_previous_generation_do_not_join_history(self):
        self.observe(0, active=[stage("forward_wait", 3)])
        self.observe(2, generation="b", active=[stage("streaming", 2)])
        self.observe(4, generation="a", active=[stage("first_content", 5)])
        self.assertEqual(len(self.history.samples), 1)
        self.assertEqual([mark.stage for mark in self.history.gateway_marks], ["streaming"])
        self.assertEqual(self.history.gateway_status(BASE + timedelta(seconds=4)), "past generation")

    def test_reconnect_retains_host_samples_but_does_not_replay_old_gateway_history(self):
        self.observe(0, active=[stage("forward_wait", 3)])
        local_time = BASE + timedelta(seconds=2)
        self.history.observe({}, connected=False, now=local_time,
                             system_sample=(local_time, observation(gpu_utilization_percent=36)["resources"]))
        self.assertEqual(self.history.latest(local_time).source, "system")
        self.assertEqual(self.history.gateway_status(local_time), "offline")
        self.observe(3, resource_seconds=1, generation="b", last=stage("completed", 8))
        self.assertEqual(self.history.latest(BASE + timedelta(seconds=3)).source, "system")
        self.assertEqual(list(self.history.gateway_marks), [])
        self.observe(4, generation="b", active=[stage("streaming", 2, request_id="req-2")])
        self.assertEqual([sample.source for sample in self.history.samples], ["system", "gateway"])
        self.assertNotEqual(self.history.samples[0].segment, self.history.samples[1].segment)
        self.assertEqual([mark.stage for mark in self.history.gateway_marks], ["streaming"])

    def test_lane_marks_multiple_observations_in_one_bucket_and_ages_out(self):
        self.observe(1, active=[stage("forward_wait", 3)])
        self.observe(2, active=[stage("first_content", 6)])
        self.assertEqual(self.history.gateway_lane(2, BASE + timedelta(seconds=12)), "* ")
        self.assertEqual(self.history.gateway_lane(2, BASE + timedelta(seconds=15)), "  ")
        self.assertEqual(self.history.gateway_status(BASE + timedelta(seconds=15)), "stale")


class HistoryTests(unittest.TestCase):
    def test_usage_derived_only_from_measured_totals_and_free(self):
        history = ResourceHistory()
        history.observe(observation(), connected=True)
        sample = history.latest(BASE)
        self.assertEqual(sample.percentages, {"cpu": 25, "gpu": 50, "ram": 75, "vram": 75})
        self.assertEqual(history.label("ram", BASE), "12.00/16.00 GiB (75.0%)")
        self.assertEqual(history.label("vram", BASE), "12.00/16.00 GiB (75.0%)")
        self.assertEqual(history.label("cpu", BASE), "25.0% (Δ2s)")

    def test_missing_totals_are_gaps_not_model_estimates(self):
        history = ResourceHistory()
        status = observation(total_ram_gib=None, total_vram_mib=None, estimated_vram_mib=12400)
        history.observe(status, connected=True)
        self.assertIsNone(history.latest(BASE).percentages["ram"])
        self.assertIsNone(history.latest(BASE).percentages["vram"])
        self.assertEqual(history.label("vram", BASE), "unavailable")

    def test_invalid_values_are_missing_but_actual_zero_is_preserved(self):
        sample = normalize_sample(BASE, {"cpu_utilization_percent": True, "gpu_utilization_percent": float("nan"), "total_ram_gib": 0, "available_ram_gib": 0, "total_vram_mib": 100, "free_vram_mib": 101})
        self.assertTrue(all(value is None for value in sample.percentages.values()))
        for value in (-1, 101, "50", float("inf")):
            self.assertIsNone(normalize_sample(BASE, {"cpu_utilization_percent": value}).percentages["cpu"])
        self.assertEqual(normalize_sample(BASE, {"gpu_utilization_percent": 0}).percentages["gpu"], 0)

    def test_duplicate_timestamp_never_adds_another_sample(self):
        history = ResourceHistory()
        self.assertTrue(history.observe(observation(), connected=True))
        self.assertFalse(history.observe(observation(cpu_utilization_percent=99), connected=True))
        self.assertEqual(len(history.samples), 1)
        self.assertEqual(history.latest(BASE).percentages["cpu"], 25)

    def test_offset_forms_of_same_instant_are_duplicates(self):
        history = ResourceHistory()
        history.observe(observation(), connected=True)
        equivalent = observation()
        equivalent["resources_sampled_at"] = "2026-11-24T04:00:00+01:00"
        self.assertFalse(history.observe(equivalent, connected=True))
        self.assertEqual(len(history.samples), 1)

    def test_stale_and_offline_do_not_ingest_or_claim_current_values(self):
        history = ResourceHistory()
        history.observe(observation(), connected=True)
        stale = observation(2, gpu_utilization_percent=99)
        stale["resources_stale"] = True
        self.assertFalse(history.observe(stale, connected=True))
        self.assertIsNone(history.latest(BASE + timedelta(seconds=2)))
        self.assertEqual(history.observation, "stale")
        self.assertFalse(history.observe(observation(4), connected=False))
        self.assertEqual(history.observation, "offline")
        self.assertEqual(len(history.samples), 1)

    def test_authoritative_timestamp_and_freshness_required(self):
        for timestamp in (None, "invalid", "2026-11-24T03:00:00"):
            with self.subTest(timestamp=timestamp):
                history = ResourceHistory()
                status = observation()
                status["resources_sampled_at"] = timestamp
                self.assertFalse(history.observe(status, connected=True))
                self.assertEqual(len(history.samples), 0)
        history = ResourceHistory()
        status = observation()
        status.pop("resources_stale")
        self.assertFalse(history.observe(status, connected=True))

    def test_old_or_future_sample_is_not_presented_as_current(self):
        history = ResourceHistory()
        history.observe(observation(), connected=True)
        self.assertIsNone(history.latest(BASE + timedelta(seconds=7)))
        self.assertEqual(history.status_label(BASE + timedelta(seconds=7)), "stale sample")
        self.assertIsNone(history.latest(BASE - timedelta(seconds=1)))
        self.assertTrue(all(value is None for value in history.series("cpu", 10, BASE - timedelta(seconds=1))))

    def test_out_of_order_samples_are_rejected(self):
        history = ResourceHistory()
        history.observe(observation(3), connected=True)
        self.assertFalse(history.observe(observation(2), connected=True))
        self.assertEqual(len(history.samples), 1)
        self.assertEqual(history.observation, "out-of-order sample")

    def test_capacity_is_bounded(self):
        history = ResourceHistory(max_points=3)
        for second in range(10):
            history.observe(observation(second), connected=True)
        self.assertEqual(len(history.samples), 3)
        self.assertEqual(history.samples[0].timestamp, BASE + timedelta(seconds=7))

    def test_time_buckets_keep_gaps_and_never_interpolate(self):
        history = ResourceHistory(window_seconds=6)
        history.observe(observation(0, cpu_utilization_percent=None, gpu_utilization_percent=0), connected=True)
        stale = observation(2, cpu_utilization_percent=100)
        stale["resources_stale"] = True
        history.observe(stale, connected=True)
        history.observe(observation(4, cpu_utilization_percent=50), connected=True)
        self.assertEqual(history.series("cpu", 6, BASE + timedelta(seconds=4)), (None, None, None, None, None, 50))
        self.assertEqual(history.series("gpu", 6, BASE + timedelta(seconds=4)), (None, None, 0, None, None, 50))

    def test_missing_cpu_does_not_erase_other_observed_metrics(self):
        history = ResourceHistory()
        history.observe(observation(cpu_utilization_percent=None, cpu_interval_seconds=None), connected=True)
        self.assertEqual(history.label("cpu", BASE), "unavailable")
        self.assertEqual(history.label("gpu", BASE), "50.0%")

    def test_chart_gaps_and_zero_are_visually_different(self):
        self.assertEqual(sparkline([None, 0, 100]), " ▁█")
        rows = bar_rows([None, 0, 50, 100], 3)
        self.assertTrue(all(row[0] == " " for row in rows))
        self.assertEqual(rows[-1][1], "▁")
        self.assertEqual(rows[0][-1], "█")
        self.assertEqual([row[2] for row in rows], [" ", "▄", "█"])
        self.assertEqual(bar_rows([75], 3), ["▂", "█", "█"])

    def test_offline_break_survives_even_when_one_bucket_merges_samples(self):
        history = ResourceHistory(window_seconds=12)
        history.observe(observation(0), connected=True)
        history.observe({}, connected=False)
        history.observe(observation(1), connected=True)
        self.assertEqual(history.series("gpu", 2, BASE + timedelta(seconds=12)), (None, None))

    def test_downsampling_never_erases_known_missing_values_or_long_gaps(self):
        history = ResourceHistory(window_seconds=20)
        history.observe(observation(0, gpu_utilization_percent=None), connected=True)
        history.observe(observation(1), connected=True)
        history.observe(observation(10), connected=True)
        end = BASE + timedelta(seconds=20)
        self.assertEqual(history.series("gpu", 2, end), (None, 50))
        self.assertEqual(history.series("cpu", 2, end), (None, 25))

    def test_plot_shows_exact_endpoints_and_does_not_draw_across_a_gap(self):
        rows = plot_rows((100, None, 0))
        self.assertEqual([row[1] for row in rows], ["·", "·", "·"])
        self.assertEqual(rows[0][0], "⠁")
        self.assertEqual(rows[-1][-1], "⡀")
        self.assertNotEqual(plot_rows((100, 0))[1][0], "·")

    def test_rich_wide_and_narrow_charts_have_units_time_and_provenance(self):
        from rich.console import Console
        history = ResourceHistory()
        for seconds in range(0, 121, 2):
            history.observe(observation(seconds, cpu_utilization_percent=(seconds * 3) % 100), connected=True)
        for width, height in ((115, 30), (80, 24)):
            with self.subTest(width=width):
                output = io.StringIO()
                Console(file=output, width=width, color_system=None).print(render_resource_history(history, width, height, now=BASE + timedelta(seconds=120)))
                text = output.getvalue()
                for expected in ("CPU", "GPU", "RAM", "VRAM", "GiB", "120s", "SYSTEM-WIDE", "100"):
                    self.assertIn(expected, text)
                self.assertIn("GW", text)
                self.assertIn("at poll", text)
                self.assertIn("not request attribution", text)
                self.assertIn("0.0s ago", text)
                if width >= 110:
                    self.assertIn("██", text)
                    self.assertNotIn("⠁", text)
                self.assertLessEqual(len(text.splitlines()), 16 if width >= 110 else 12)


if __name__ == "__main__":
    unittest.main()
