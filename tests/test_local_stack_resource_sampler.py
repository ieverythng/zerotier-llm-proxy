"""Resource-chart observations without workloads, sleeps, drivers or subprocesses."""
import io
import itertools
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local_stack import runtime
from local_stack.registry import StackError


class CpuIntervalTests(unittest.TestCase):
    def sampler(self, readings, times=None):
        values = iter(readings)
        ticks = iter(times) if times is not None else itertools.count(1)
        return runtime.AggregateCpuSampler(reader=lambda: next(values), clock=lambda: next(ticks))

    def test_first_observation_is_unavailable_not_zero(self):
        sample = self.sampler([(50, 100)]).sample()
        self.assertEqual(sample, {'cpu_utilization_percent': None, 'cpu_interval_seconds': None})

    def test_interval_uses_busy_delta_not_cumulative_average(self):
        sampler = self.sampler([(1000, 2000), (1020, 2100)], [10, 12])
        sampler.sample()
        self.assertEqual(sampler.sample(), {'cpu_utilization_percent': 80.0, 'cpu_interval_seconds': 2})

    def test_duplicate_counter_is_a_gap_and_preserves_advancing_baseline(self):
        sampler = self.sampler([(100, 200), (100, 200), (120, 300)], [10, 11, 12])
        sampler.sample()
        self.assertIsNone(sampler.sample()['cpu_utilization_percent'])
        self.assertEqual(sampler.sample(), {'cpu_utilization_percent': 80.0, 'cpu_interval_seconds': 2})

    def test_counter_reset_rebaselines_without_negative_or_spurious_usage(self):
        sampler = self.sampler([(1000, 2000), (100, 200), (140, 300)])
        self.assertIsNone(sampler.sample()['cpu_utilization_percent'])
        self.assertIsNone(sampler.sample()['cpu_utilization_percent'])
        self.assertEqual(sampler.sample()['cpu_utilization_percent'], 60.0)

    def test_inconsistent_idle_delta_is_not_clamped_to_fake_zero(self):
        sampler = self.sampler([(10, 100), (100, 110)])
        sampler.sample()
        self.assertIsNone(sampler.sample()['cpu_utilization_percent'])

    def test_real_zero_and_full_busy_are_valid_values(self):
        for readings, expected in (([(10, 100), (110, 200)], 0), ([(10, 100), (10, 200)], 100)):
            with self.subTest(expected=expected):
                sampler = self.sampler(readings)
                sampler.sample()
                self.assertEqual(sampler.sample()['cpu_utilization_percent'], expected)

    def test_invalid_or_missing_counter_is_gap_and_requires_new_baseline(self):
        for invalid in (None, (-1, 5), (10, 5), (True, 5), (1.5, 5), [1, 5]):
            with self.subTest(invalid=invalid):
                sampler = self.sampler([(10, 100), invalid, (30, 200), (60, 300)])
                sampler.sample()
                self.assertIsNone(sampler.sample()['cpu_utilization_percent'])
                self.assertIsNone(sampler.sample()['cpu_utilization_percent'])
                self.assertEqual(sampler.sample()['cpu_utilization_percent'], 70)

    def test_backward_duplicate_and_nonfinite_clock_never_publish_invalid_intervals(self):
        for times in ([10, 9], [10, 10], [10, float('nan')], [10, float('inf')]):
            sampler = self.sampler([(10, 100), (30, 200)], times)
            sampler.sample()
            self.assertIsNone(sampler.sample()['cpu_utilization_percent'])

    def test_provider_exception_does_not_raise_or_publish_fake_zero(self):
        sampler = runtime.AggregateCpuSampler(reader=Mock(side_effect=OSError('private provider error')))
        self.assertEqual(sampler.sample(), {'cpu_utilization_percent': None, 'cpu_interval_seconds': None})

    def test_concurrent_calls_have_one_ordered_baseline(self):
        counters, times = itertools.count(1), itertools.count(1)
        def reader():
            sample = next(counters)
            return sample * 40, sample * 100
        sampler = runtime.AggregateCpuSampler(reader=reader, clock=lambda: next(times))
        with ThreadPoolExecutor(max_workers=8) as pool:
            samples = list(pool.map(lambda _: sampler.sample(), range(100)))
        valid = [sample for sample in samples if sample['cpu_utilization_percent'] is not None]
        self.assertEqual(len(valid), 99)
        self.assertTrue(all(sample['cpu_utilization_percent'] == 60 for sample in valid))
        self.assertTrue(all(sample['cpu_interval_seconds'] == 1 for sample in valid))


class CpuReaderTests(unittest.TestCase):
    def test_windows_kernel_includes_idle_no_double_count(self):
        def system_times(idle, kernel, user):
            idle._obj.value, kernel._obj.value, user._obj.value = 30, 100, 50
            return 1
        api = Mock(side_effect=system_times)
        dll = SimpleNamespace(kernel32=SimpleNamespace(GetSystemTimes=api))
        with patch.object(runtime, 'os', SimpleNamespace(name='nt', cpu_count=lambda: 16)), patch.object(runtime.ctypes, 'windll', dll, create=True):
            self.assertEqual(runtime._system_cpu_times(), (30, 150))
        api.assert_called_once()

    def test_windows_failure_or_group_scoped_system_is_unavailable(self):
        api = Mock(return_value=0)
        dll = SimpleNamespace(kernel32=SimpleNamespace(GetSystemTimes=api))
        with patch.object(runtime, 'os', SimpleNamespace(name='nt', cpu_count=lambda: 16)), patch.object(runtime.ctypes, 'windll', dll, create=True):
            self.assertIsNone(runtime._system_cpu_times())
        for count in (None, 128):
            with patch.object(runtime, 'os', SimpleNamespace(name='nt', cpu_count=lambda: count)), patch.object(runtime.ctypes, 'windll', dll, create=True):
                self.assertIsNone(runtime._system_cpu_times())

    def test_linux_aggregate_excludes_double_counted_guest_and_includes_iowait_idle(self):
        line = 'cpu 100 20 30 400 50 10 5 2 80 10\ncpu0 1 1 1 1\n'
        with patch.object(runtime, 'os', SimpleNamespace(name='posix')), patch.object(runtime.Path, 'open', return_value=io.StringIO(line)):
            self.assertEqual(runtime._system_cpu_times(), (450, 617))

    def test_linux_missing_or_invalid_aggregate_is_unavailable(self):
        for text in ('cpu0 1 2 3 4\n', 'cpu invalid 2 3 4\n', 'cpu 1 -2 3 4\n', ''):
            with self.subTest(text=text), patch.object(runtime, 'os', SimpleNamespace(name='posix')), patch.object(runtime.Path, 'open', return_value=io.StringIO(text)):
                self.assertIsNone(runtime._system_cpu_times())
        with patch.object(runtime, 'os', SimpleNamespace(name='posix')), patch.object(runtime.Path, 'open', side_effect=OSError('missing')):
            self.assertIsNone(runtime._system_cpu_times())


class ResourceSnapshotTests(unittest.TestCase):
    def snapshot(self, output='3200, 16303, 17\n', ram=(6.25, 15.925), cpu=None):
        cpu = cpu or {'cpu_utilization_percent': 42.5, 'cpu_interval_seconds': 2}
        runner = Mock(return_value=SimpleNamespace(stdout=output))
        with patch.object(runtime, '_memory_snapshot', return_value=ram), patch.object(runtime.subprocess, 'run', runner), patch.object(runtime, '_CPU_SAMPLER', SimpleNamespace(sample=Mock(return_value=cpu))), patch.object(runtime.time, 'sleep', side_effect=AssertionError('must not sleep')):
            result = runtime.resource_snapshot()
        return result, runner

    def test_existing_admission_values_preserved_and_real_totals_added(self):
        result, runner = self.snapshot()
        self.assertEqual(result['available_ram_gib'], 6.25)
        self.assertEqual(result['free_vram_mib'], 3200)
        self.assertEqual(result['gpu_utilization_percent'], 17)
        self.assertEqual(result['total_ram_gib'], 15.925)
        self.assertEqual(result['total_vram_mib'], 16303)
        self.assertEqual(result['cpu_utilization_percent'], 42.5)
        self.assertEqual(result['cpu_interval_seconds'], 2)
        runner.assert_called_once()
        self.assertIn('--query-gpu=memory.free,memory.total,utilization.gpu', runner.call_args.args[0])
        self.assertEqual(runner.call_args.kwargs['timeout'], 10)

    def test_unknown_cpu_or_capacity_stays_none_not_zero_and_does_not_disable_free_guard(self):
        result, _ = self.snapshot('3200, N/A, 17\n', ram=(6.25, None), cpu={'cpu_utilization_percent': None, 'cpu_interval_seconds': None})
        self.assertIsNone(result['total_ram_gib'])
        self.assertIsNone(result['total_vram_mib'])
        self.assertIsNone(result['cpu_utilization_percent'])
        self.assertEqual(result['free_vram_mib'], 3200)

    def test_required_gpu_fields_and_single_device_remain_fail_closed(self):
        for output in ('N/A, 16303, 17\n', '3200, 16303, N/A\n', '-1,16303,17\n', '3200,16303,101\n', '3200,16303,17\n1000,8000,4\n'):
            with self.subTest(output=output), self.assertRaises(StackError):
                self.snapshot(output)

    def test_memory_api_required_failure_stays_fatal(self):
        with patch.object(runtime, '_memory_snapshot', side_effect=StackError('unavailable', 503)), patch.object(runtime.subprocess, 'run') as runner:
            with self.assertRaises(StackError):
                runtime.resource_snapshot()
            runner.assert_not_called()

    def test_memory_total_uses_same_windows_snapshot_not_a_second_call(self):
        def memory_status(pointer):
            pointer._obj.avail_phys = 6 * (1024 ** 3)
            pointer._obj.total_phys = 16 * (1024 ** 3)
            return 1
        api = Mock(side_effect=memory_status)
        dll = SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=api))
        with patch.object(runtime, 'os', SimpleNamespace(name='nt')), patch.object(runtime.ctypes, 'windll', dll, create=True):
            self.assertEqual(runtime._memory_snapshot(), (6, 16))
        api.assert_called_once()


if __name__ == '__main__':
    unittest.main()
