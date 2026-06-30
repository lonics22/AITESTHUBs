from django.test import TestCase
from .ai_types import validate_field, fix_field, validate_and_fix_record
from .ai_context import retrieve_project_context


class AITypesTest(TestCase):
    def test_email_validation_valid(self):
        self.assertTrue(validate_field('email', 'test@example.com'))

    def test_email_validation_invalid(self):
        self.assertFalse(validate_field('email', 'not-an-email'))

    def test_email_fixer_returns_valid(self):
        result = fix_field('email', 'bad-email')
        self.assertTrue(validate_field('email', result))

    def test_phone_fixer(self):
        result = fix_field('phone', '12345')
        self.assertTrue(validate_field('phone', result))

    def test_validate_and_fix_record(self):
        record = {'email': 'bad', 'phone': '12345'}
        defs = [{'name': 'email', 'type': 'email'}, {'name': 'phone', 'type': 'phone'}]
        fixed = validate_and_fix_record(record, defs)
        self.assertTrue(validate_field('email', fixed['email']))
        self.assertTrue(validate_field('phone', fixed['phone']))


class AIContextTest(TestCase):
    def test_retrieve_empty_project(self):
        context = retrieve_project_context(99999)
        self.assertIn('related_entities', context)
        self.assertIn('available_ids', context)
        self.assertIsInstance(context['related_entities'], list)
        self.assertIsInstance(context['available_ids'], list)


class AIAgentTest(TestCase):
    def test_route_after_classify_all_auto(self):
        from .ai_agent import route_after_classify
        state = {
            'error': None,
            'classification': {
                'classification': [{'field': 'email', 'type': 'auto', 'reason': ''}],
                'manual_fields': [],
                'context_fields': [],
            }
        }
        self.assertEqual(route_after_classify(state), 'generate_data')

    def test_route_after_classify_has_manual(self):
        from .ai_agent import route_after_classify
        state = {
            'error': None,
            'classification': {
                'classification': [{'field': 'username', 'type': 'manual', 'reason': ''}],
                'manual_fields': [{'field': 'username', 'prompt': '请输入...'}],
                'context_fields': [],
            }
        }
        self.assertEqual(route_after_classify(state), 'wait_for_user')

    def test_route_after_classify_has_context(self):
        from .ai_agent import route_after_classify
        state = {
            'error': None,
            'classification': {
                'classification': [{'field': 'token', 'type': 'context_ref', 'reason': ''}],
                'manual_fields': [],
                'context_fields': [{'field': 'token', 'prompt': '需要...'}],
            }
        }
        self.assertEqual(route_after_classify(state), 'retrieve_context')
