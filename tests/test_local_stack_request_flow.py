"""Metadata telemetry tests; no real backend, GPU, services or credentials."""
import io
import json
import os
import threading
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from local_stack.request_flow import ContentPresenceProbe, OwnedBackendMetrics, RequestFlow, parse_backend_metrics


class Response:
    status = 200
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, count):
        return self.body[:count]


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.flow = RequestFlow('test-generation', direct=False, clock=lambda: self.now, utc=lambda: '2026-11-24T00:00:00+00:00')

    def test_admission_complete_and_rejection_counts(self):
        request = self.flow.begin('inference')
        self.flow.describe(request, 'inkling2-mini', True)
        self.assertEqual(self.flow.snapshot()['counters']['active'], 0)
        self.flow.accept(request)
        self.flow.stage(request, 'load_or_validate')
        self.assertEqual(self.flow.snapshot()['counters']['active'], 1)
        self.flow.stage(request, 'forward_wait')
        self.now += .25
        self.flow.first_content(request)
        self.flow.forwarded(request, 128)
        self.flow.finish(request, 'completed')
        rejected = self.flow.begin('inference')
        self.flow.finish(rejected, 'rejected', 'busy')
        snapshot = self.flow.snapshot()
        self.assertEqual(snapshot['counters'], {'ingress': 2, 'accepted': 1, 'active': 0, 'completed': 1, 'failed': 0, 'rejected': 1, 'cancelled': 0})
        self.assertEqual(snapshot['last']['error_kind'], 'busy')
        self.assertEqual(snapshot['active'], [])

    def test_finish_is_idempotent_and_cancelled_is_local_only(self):
        request = self.flow.begin('inference')
        self.flow.accept(request)
        self.flow.finish(request, 'cancelled', 'local_disconnect')
        self.flow.finish(request, 'failed', 'internal')
        counters = self.flow.snapshot()['counters']
        self.assertEqual(counters['cancelled'], 1)
        self.assertEqual(counters['failed'], 0)
        self.assertEqual(counters['active'], 0)

    def test_unknown_payload_fields_cannot_enter_snapshot(self):
        request = self.flow.begin('inference')
        self.flow.describe(request, 'saluki27b')
        request['prompt'] = 'PRIVATE_PROMPT'
        request['output'] = 'PRIVATE_OUTPUT'
        request['authorization'] = 'PRIVATE_KEY'
        self.flow.accept(request)
        self.flow.finish(request, 'completed')
        snapshot = json.dumps(self.flow.snapshot())
        for private in ('PRIVATE_PROMPT', 'PRIVATE_OUTPUT', 'PRIVATE_KEY', 'authorization'):
            self.assertNotIn(private, snapshot)
        with self.assertRaises(ValueError):
            self.flow.describe(self.flow.begin('inference'), 'C:/secret/file')
        with self.assertRaises(ValueError):
            self.flow.finish(self.flow.begin('inference'), 'failed', 'raw exception secret')

    def test_proxy_activity_is_inferred_and_never_claimed_sequential(self):
        request = self.flow.begin('inference')
        self.flow.accept(request)
        self.flow.stage(request, 'load_or_validate')
        self.assertEqual(self.flow.snapshot()['nodes']['headroom']['state'], 'unknown')
        self.flow.stage(request, 'forward_wait')
        nodes = self.flow.snapshot()['nodes']
        self.assertEqual(nodes['headroom'], {'state': 'in_flight', 'source': 'inferred'})
        self.assertEqual(nodes['litellm'], nodes['headroom'])
        self.flow.finish(request, 'completed')
        self.assertEqual(self.flow.snapshot()['nodes']['headroom']['state'], 'unknown')

    def test_direct_and_native_benchmark_bypass_proxy_nodes(self):
        for direct, kind in ((True, 'inference'), (False, 'benchmark')):
            flow = RequestFlow('g', direct=direct)
            request = flow.begin(kind)
            flow.accept(request)
            flow.stage(request, 'benchmark' if kind == 'benchmark' else 'forward_wait')
            self.assertEqual(flow.snapshot()['nodes']['headroom']['state'], 'bypassed')
            self.assertEqual(flow.snapshot()['nodes']['litellm']['state'], 'bypassed')

    def test_pending_metadata_is_bounded_without_hiding_admitted_request(self):
        for _ in range(1000):
            latest = self.flow.begin('inference')
        self.assertEqual(len(self.flow.records), 64)
        self.assertEqual(self.flow.snapshot()['counters']['ingress'], 1000)
        self.flow.accept(latest)
        self.assertEqual(len(self.flow.records), 64)
        self.assertEqual(self.flow.snapshot()['active'][0]['id'], latest['id'])

    def test_snapshot_observation_does_not_create_activity(self):
        first = self.flow.snapshot()
        for _ in range(100):
            self.assertEqual(self.flow.snapshot()['counters'], first['counters'])
        self.assertEqual(first['generation'], 'test-generation')
        self.assertEqual(first['route'], 'headroom_litellm')
        self.assertEqual(first['backend']['state'], 'unknown')
        self.assertTrue(first['backend']['stale'])

    def test_generation_instances_do_not_share_counters(self):
        self.flow.begin('inference')
        other = RequestFlow('next-generation')
        self.assertEqual(other.snapshot()['counters']['ingress'], 0)


class ContentProbeTests(unittest.TestCase):
    def test_only_actual_content_not_reasoning_or_tool_calls(self):
        probe = ContentPresenceProbe(True)
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE_REASONING"}}]}\n\n'))
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call"}]}}]}\n\n'))
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"content":""}}]}\n\n'))
        self.assertTrue(probe.feed(b'data: {"choices":[{"delta":{"content":"PRIVATE_ANSWER"}}]}\n\n'))
        self.assertEqual(probe.buffer, bytearray())
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"content":"later"}}]}\n\n'))

    def test_split_sse_line_and_nonstream_json(self):
        probe = ContentPresenceProbe(True)
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"cont'))
        self.assertTrue(probe.feed(b'ent":"answer"}}]}\n\n'))
        probe = ContentPresenceProbe(False)
        self.assertFalse(probe.feed(b'{"choices":[{"message":{"content":"answer"}}]}'))
        self.assertTrue(probe.finish())
        self.assertEqual(probe.buffer, bytearray())

    def test_huge_or_invalid_payload_is_unobserved_and_bounded(self):
        probe = ContentPresenceProbe(True)
        self.assertFalse(probe.feed(b'x' * 20000))
        self.assertTrue(probe.disabled)
        self.assertEqual(len(probe.buffer), 0)
        self.assertFalse(probe.feed(b'data: {"choices":[{"delta":{"content":"x"}}]}\n'))
        probe.close()
        probe = ContentPresenceProbe(False)
        probe.feed(b'not-json')
        self.assertFalse(probe.finish())


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        identity = {'created': '100', 'executable': 'PRIVATE_PATH/llama-server.exe'}
        self.record = {'pid': 12, 'identity': identity, 'model': 'inkling2-mini', 'context': 8192, 'ready': True}
        self.runtime = Mock(record=self.record, backend_port=18081)
        self.runtime.identify.return_value = identity
        self.opener = Mock()
        self.opener.open.return_value = Response(b'llamacpp:requests_processing 1\nllamacpp:prompt_tokens_total 1234\nllamacpp:tokens_predicted_total 56\nprivate:secret 999\n')
        self.sampler = OwnedBackendMetrics(self.runtime, opener=self.opener, clock=lambda: self.now, utc=lambda: 'now')

    def refresh(self):
        owned = self.sampler._owned()
        self.sampler.epoch = self.sampler._epoch(owned)
        self.sampler.pending = True
        with patch.dict(os.environ, {'LLAMA_CPP_API_KEY': 'PRIVATE_KEY'}):
            self.sampler._refresh(owned, self.sampler.epoch)

    def test_only_allowlisted_finite_backend_metrics_exported(self):
        self.refresh()
        snapshot = self.sampler.snapshot()
        self.assertEqual(snapshot['state'], 'processing')
        self.assertFalse(snapshot['stale'])
        self.assertEqual(snapshot['prompt_tokens_total'], 1234)
        self.assertEqual(snapshot['generated_tokens_total'], 56)
        self.assertIs(type(snapshot['processing']), int)
        serialized = json.dumps(snapshot)
        for secret in ('PRIVATE_KEY', 'PRIVATE_PATH', 'private:secret', 'llamacpp:'):
            self.assertNotIn(secret, serialized)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:18081/metrics')
        self.assertEqual(self.opener.open.call_args.kwargs['timeout'], .4)
        self.assertEqual(request.get_header('Authorization'), 'Bearer PRIVATE_KEY')

    def test_failed_or_stale_metric_does_not_report_processing(self):
        self.refresh()
        self.now += 6
        self.assertEqual(self.sampler.snapshot()['state'], 'unknown')
        self.assertTrue(self.sampler.snapshot()['stale'])
        self.opener.open.side_effect = OSError('PRIVATE_ERROR')
        self.refresh()
        self.assertEqual(self.sampler.snapshot()['state'], 'unknown')
        self.assertNotIn('PRIVATE_ERROR', json.dumps(self.sampler.snapshot()))

    def test_model_swap_during_read_discards_old_epoch(self):
        def swap(*args, **kwargs):
            self.runtime.record = None
            return Response(b'llamacpp:requests_processing 1\n')
        self.opener.open.side_effect = swap
        self.refresh()
        self.assertTrue(self.sampler.snapshot()['stale'])
        self.assertIsNone(self.sampler.snapshot()['processing'])

    def test_unknown_or_unowned_backend_is_never_probed(self):
        self.runtime.identify.return_value = {'created': 'reused'}
        self.sampler.maybe_refresh()
        self.opener.open.assert_not_called()
        self.assertEqual(self.sampler.snapshot()['state'], 'unknown')

    def test_no_concurrent_or_high_rate_background_sampling(self):
        self.sampler.pending = True
        with patch('local_stack.request_flow.threading.Thread') as thread:
            self.sampler.maybe_refresh()
            thread.assert_not_called()
        self.sampler.pending = False
        self.sampler.next_poll = self.now + 2
        with patch('local_stack.request_flow.threading.Thread') as thread:
            self.sampler.maybe_refresh()
            thread.assert_not_called()

    def test_metric_parser_ignores_labels_unknown_and_nonfinite_values(self):
        parsed = parse_backend_metrics('llamacpp:requests_processing 0\nllamacpp:prompt_tokens_total{secret="x"} 2\nllamacpp:tokens_predicted_total 1e999\nunknown 99\n')
        self.assertEqual(parsed, {'processing': 0})
        with self.assertRaises(ValueError):
            parse_backend_metrics('llamacpp:requests_processing 0\nllamacpp:requests_processing 1\n')


if __name__ == '__main__':
    unittest.main()
