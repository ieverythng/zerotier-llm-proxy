"""No endpoints or OS processes needed to test the scratch advisory."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_stack.scratch_observation import ENGINES, observe_scratch


class ScratchObservationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'active-worker.json'
        self.now = datetime(2026, 11, 25, 1, 30, tzinfo=timezone.utc)
        self.worker = {'executable': str(ENGINES / 'llama-b12651' / 'llama-server.exe'), 'created': '12'}
        self.runner = {'executable': 'python.exe', 'created': '10'}
        self.valid = {'version': 1, 'alias': 'inkling2-mini', 'context': 150016,
                      'port': 18181, 'observed_at': self.now.isoformat(),
                      'ready': True, 'phase': 'prompt_147968',
                      'worker': {'pid': 42, 'identity': self.worker},
                      'runner': {'pid': 41, 'identity': self.runner}}

    def observe(self, value, **kwargs):
        self.path.write_text(json.dumps(value), encoding='utf8')
        opts = {'now': self.now, 'identify': {42: self.worker, 41: self.runner}.get,
                'parent_of': lambda _: 41, **kwargs}
        return observe_scratch(self.path, **opts)

    def test_fresh_identity_returns_only_advisory_and_not_path_or_control(self):
        result = self.observe({**self.valid, 'secret': 'not for display'})
        self.assertEqual(result['context'], 150016)
        self.assertEqual(result['phase'], '147,968-token timing request')
        self.assertEqual(set(result), {'source', 'alias', 'context', 'phase', 'observed_at', 'pid', 'ready'})

    def test_refuses_stale_recycled_wrong_route_or_impossible_phase(self):
        for value, overrides in (
            ({**self.valid, 'observed_at': (self.now - timedelta(seconds=9)).isoformat()}, {}),
            (self.valid, {'identify': lambda _: None}),
            (self.valid, {'parent_of': lambda _: 7}),
            ({**self.valid, 'port': 18081}, {}),
            ({**self.valid, 'phase': 'prompt_150016'}, {}),
            ({**self.valid, 'phase': 'finished'}, {}),
            ({**self.valid, 'phase': ['unknown']}, {}),
        ):
            with self.subTest(phase=value.get('phase'), overrides=bool(overrides)):
                self.assertIsNone(self.observe(value, **overrides))

    def test_malformed_oversized_and_missing_are_unavailable(self):
        self.assertIsNone(observe_scratch(self.path, now=self.now))
        self.assertIsNone(self.observe([]))
        self.assertIsNone(self.observe({'bad': 'x' * 4096}))


if __name__ == '__main__':
    unittest.main()
