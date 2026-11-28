"""Historical projections must preserve reported amounts, units and scope."""
import unittest
from pathlib import Path

from local_stack.qualification_evidence import QUALIFICATIONS


class QualificationEvidenceTests(unittest.TestCase):
    def test_every_bounded_record_points_to_a_repository_report(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(len(QUALIFICATIONS), 10)
        self.assertEqual(len({record.scenario for record in QUALIFICATIONS}), 10)
        for record in QUALIFICATIONS:
            with self.subTest(record=record.scenario):
                report, fragment = record.report.split('#', 1)
                self.assertTrue(report.startswith('docs/reports/'))
                self.assertTrue((root / report).is_file())
                self.assertTrue(fragment)
                if record.actual_input is not None:
                    self.assertLess(record.actual_input, record.allocation)

    def test_mixed_recall_is_not_the_text_timing_run(self):
        by_scenario = {record.scenario: record for record in QUALIFICATIONS}
        text = by_scenario['Inkling 150K timing']
        mixed = by_scenario['Inkling 150K mixed recall']
        self.assertEqual((text.actual_input, text.first_text_s), (147968, 597.11))
        self.assertEqual((mixed.actual_input, mixed.actual_output, mixed.wall_s), (139742, 323, 556.42))
        self.assertIsNone(mixed.first_text_s)
        for record in (text, mixed):
            self.assertLess(record.minimum_free_vram_mib, 2048)
            self.assertIn('normal', record.outcome)
        self.assertIsNone(by_scenario['Inkling 100K mixed recall'].first_text_s)
        capacity=by_scenario['Inkling 200K capacity']
        self.assertIsNone(capacity.actual_input)
        self.assertIsNone(capacity.first_text_s)
        self.assertIn('not default',capacity.outcome)
        quick=by_scenario['Inkling 150K default quick']
        self.assertEqual((quick.actual_input,quick.actual_output),(1024,64))
        self.assertIn('short-input',quick.outcome)


if __name__ == '__main__':
    unittest.main()
