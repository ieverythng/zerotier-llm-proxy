"""Pure registration workflow regression tests; no hardware, host or gateway."""
import unittest

from local_stack.registration_workflow import RegistrationDraft, registration_options


def profiled_limit(profile):
    return profile.get('maximum_context', 8192)


class RegistrationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = {'id': 'matching-text', 'engine': 'approved/llama-server.exe', 'minimum_context': 4096}
        self.vision = {**self.text, 'id': 'matching-vision', 'input': ['text', 'image']}
        self.model = {'id': 'new-model', 'name': 'New model', 'path': 'new-model.gguf',
                      'engine': self.text['engine'], 'context': 8192, 'context_presets': [8192],
                      'max_output_tokens': 2048, 'input': ['text'], 'validation': 'unverified',
                      'context_verified': False}

    def test_choice_keeps_only_profiles_that_fit_provisional_context(self):
        options = registration_options([
            {**self.text, 'id': 'fixed-long', 'minimum_context': 100096, 'maximum_context': 100096},
            {**self.text, 'id': 'large-vision', 'minimum_context': 32768},
            self.text,
            {**self.text, 'id': 'no-engine', 'engine': ' '},
            self.vision,
        ], profiled_limit)
        self.assertEqual([o.template['id'] for o in options], ['matching-text', 'matching-vision'])
        self.assertEqual([(o.minimum_context, o.maximum_context) for o in options], [(4096, 8192)] * 2)

    def test_choice_alignment_never_rounds_past_template_limit(self):
        unaligned = {**self.text, 'minimum_context': 1025, 'maximum_context': 1535}
        options = registration_options([unaligned, {**unaligned, 'maximum_context': 1279}], profiled_limit)
        self.assertEqual([(o.minimum_context, o.maximum_context) for o in options], [(1280, 1280)])

    def test_cap_applies_only_to_provisional_workflow(self):
        big = {**self.text, 'maximum_context': 262144}
        option, = registration_options([big], profiled_limit)
        self.assertEqual(option.maximum_context, 8192)
        self.assertEqual(big['maximum_context'], 262144)

    def test_exact_confirmation_returns_snapshot_and_is_not_affected_by_mutation(self):
        option, = registration_options([self.text], profiled_limit)
        draft = RegistrationDraft.capture(option, self.model)
        self.model['name'] = 'Changed'
        self.model['context_presets'].append(16384)
        reviewed = dict(draft.review_fields())
        self.assertEqual(reviewed['Context'], '8,192 (unverified)')
        self.assertEqual(reviewed['Max output'], '2,048')
        self.assertEqual(reviewed['Inherited profile'], 'matching-text')
        for choice in ('', 'Y', 'register', ' REGISTER', 'REGISTER '):
            self.assertIsNone(draft.confirmed_payload(choice))
        confirmed = draft.confirmed_payload('REGISTER')
        self.assertEqual(confirmed['name'], 'New model')
        self.assertEqual(confirmed['context_presets'], [8192])
        confirmed['context_presets'].clear()
        self.assertEqual(draft.confirmed_payload('REGISTER')['context_presets'], [8192])

    def test_projector_is_displayed_without_inheriting_one(self):
        option, = registration_options([self.vision], profiled_limit)
        draft = RegistrationDraft.capture(option, {**self.model, 'input': ['text', 'image'], 'mmproj': 'explicit-mmproj.gguf'})
        review = dict(draft.review_fields())
        self.assertEqual(review['Projector'], 'explicit-mmproj.gguf')
        self.assertEqual(review['Input'], 'text, image')
        self.assertEqual(draft.confirmed_payload('REGISTER')['mmproj'], 'explicit-mmproj.gguf')

    def test_cannot_confirm_mismatched_or_preverified_drafts(self):
        option, = registration_options([self.text], profiled_limit)
        for override, reason in (({'context': 8193}, 'context'), ({'context': 8000}, 'context'),
                                 ({'context': 2048}, 'context'), ({'engine': 'different'}, 'engine'),
                                 ({'context_verified': True}, 'unverified'), ({'validation': 'passed'}, 'unverified')):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, reason):
                RegistrationDraft.capture(option, {**self.model, **override})
