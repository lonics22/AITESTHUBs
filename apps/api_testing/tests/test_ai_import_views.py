# -*- coding: utf-8 -*-
"""Tests for apps.api_testing.views.AIImportViewSet.

Run with:
    python manage.py test apps.api_testing.tests.test_ai_import_views --verbosity=2
"""
import json
from io import BytesIO

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from apps.api_testing.models import AIImportTask, ApiProject, ApiCollection


# ============================================================================
# Fixture helpers
# ============================================================================

def _make_swagger2_fixture() -> dict:
    """Create a minimal Swagger 2.0 document for testing."""
    return {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "basePath": "/api",
        "host": "api.example.com",
        "paths": {
            "/users": {
                "get": {
                    "tags": ["users"],
                    "summary": "List users",
                    "operationId": "listUsers",
                    "parameters": [
                        {"name": "page", "in": "query", "type": "integer",
                         "description": "Page number", "required": False},
                        {"name": "search", "in": "query", "type": "string",
                         "description": "Search keyword", "required": False},
                    ],
                    "responses": {
                        "200": {"description": "A list of users"},
                    },
                },
                "post": {
                    "tags": ["users"],
                    "summary": "Create user",
                    "operationId": "createUser",
                    "parameters": [
                        {"name": "body", "in": "body", "required": True,
                         "schema": {
                             "type": "object",
                             "properties": {
                                 "name": {"type": "string"},
                                 "email": {"type": "string"},
                             },
                             "required": ["name", "email"],
                         }},
                    ],
                    "responses": {
                        "201": {"description": "Created"},
                    },
                },
            },
            "/users/{id}": {
                "get": {
                    "tags": ["users"],
                    "summary": "Get user by ID",
                    "operationId": "getUser",
                    "parameters": [
                        {"name": "id", "in": "path", "type": "integer",
                         "description": "User ID", "required": True},
                    ],
                    "responses": {
                        "200": {"description": "A user"},
                    },
                },
            },
        },
    }


def _make_swagger2_bytes() -> bytes:
    return json.dumps(_make_swagger2_fixture()).encode('utf-8')


# ============================================================================
# Base test case
# ============================================================================

@override_settings(USE_SQLITE=True)
class AIImportViewTestBase(TestCase):
    """Base class with authenticated client and helper methods."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.swagger_bytes = _make_swagger2_bytes()

    def setUp(self):
        super().setUp()
        self.user = self._create_user()
        self.client.force_login(self.user)

    def _create_user(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return User.objects.create_user(
            username='test_importer',
            password='testpass123',
        )

    def _upload_file(self, content=None, filename='swagger.json'):
        """Helper: POST /upload/ with a file."""
        if content is None:
            content = self.swagger_bytes
        uploaded = SimpleUploadedFile(filename, content, content_type='application/json')
        return self.client.post(
            reverse('aiimport-upload'),
            {'file': uploaded},
            format='multipart',
        )

    def _create_project(self):
        """Create a test project owned by the test user."""
        return ApiProject.objects.create(
            name='Test Project',
            description='A project for testing',
            project_type='HTTP',
            status='IN_PROGRESS',
            owner=self.user,
        )


# ============================================================================
# Upload tests
# ============================================================================

class TestAIImportUpload(AIImportViewTestBase):

    def test_upload_swagger2_success(self):
        """Upload a valid Swagger 2.0 document should create a task."""
        response = self._upload_file()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['doc_type'], 'swagger2')
        self.assertEqual(data['status'], 'waiting_user')
        self.assertEqual(data['progress'], 40)
        self.assertEqual(len(data['parsed_endpoints']), 3)
        self.assertGreater(len(data['ai_questions']), 0)
        self.assertEqual(data['ai_classification']['endpoint_count'], 3)
        self.assertEqual(data['created_by_name'], self.user.username)

    def test_upload_requires_auth(self):
        """Unauthenticated upload should return 401."""
        self.client.logout()
        response = self._upload_file()
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_upload_invalid_json(self):
        """Upload invalid JSON should return 400."""
        response = self._upload_file(content=b'not json', filename='bad.json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('不是有效的 JSON', response.json()['error'])

    def test_upload_unknown_format(self):
        """Upload valid JSON but unknown format should return 400."""
        response = self._upload_file(
            content=json.dumps({"foo": "bar"}).encode(),
            filename='unknown.json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Unable to detect', response.json()['error'])

    def test_upload_no_file(self):
        """Upload without a file should return 400."""
        response = self.client.post(
            reverse('aiimport-upload'),
            {},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ============================================================================
# Retrieve tests
# ============================================================================

class TestAIImportRetrieve(AIImportViewTestBase):

    def test_retrieve_task(self):
        """GET /{id}/ should return task data."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        response = self.client.get(reverse('aiimport-detail', args=[task_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['id'], task_id)
        self.assertEqual(data['doc_type'], 'swagger2')
        self.assertIn('project_name', data)
        self.assertIn('created_by_name', data)

    def test_retrieve_not_found(self):
        """GET /99999/ should return 404."""
        response = self.client.get(reverse('aiimport-detail', args=[99999]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ============================================================================
# Configure tests
# ============================================================================

class TestAIImportConfigure(AIImportViewTestBase):

    def test_configure_success(self):
        """POST /{id}/configure/ should set project and return task."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        response = self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['project'], project.id)
        self.assertEqual(data['project_name'], project.name)
        self.assertTrue(data['auto_structure'])

    def test_configure_invalid_project(self):
        """Configure with non-existent project should return 404."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        response = self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': 99999},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_configure_missing_project_id(self):
        """Configure without project_id should return 400."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        response = self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'auto_structure': False},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_configure_with_target_collection(self):
        """Configure with target_collection_id should work."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()
        collection = ApiCollection.objects.create(
            project=project, name='Existing Collection',
        )

        response = self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {
                'project_id': project.id,
                'auto_structure': False,
                'target_collection_id': collection.id,
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['target_collection_id'], collection.id)
        self.assertFalse(data['auto_structure'])


# ============================================================================
# Answers tests
# ============================================================================

class TestAIImportAnswers(AIImportViewTestBase):

    def _setup_configured_task(self):
        """Upload + configure a task, ready for answers submission."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )
        return AIImportTask.objects.get(id=task_id)

    def test_answers_success(self):
        """Submit answers should trigger request generation."""
        task = self._setup_configured_task()

        response = self.client.post(
            reverse('aiimport-answers', args=[task.id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {'api.example.com': '{{BASE_URL}}'},
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # The service may complete or fail depending on LLM availability
        self.assertIn(data['status'], ('completed', 'failed'))
        self.assertEqual(data['progress'], 100)

    def test_answers_missing_user_answers(self):
        """Submit answers without user_answers should return 400."""
        task = self._setup_configured_task()
        response = self.client.post(
            reverse('aiimport-answers', args=[task.id]),
            {},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_answers_generates_requests(self):
        """After answers, generated_summary should contain requests."""
        task = self._setup_configured_task()

        self.client.post(
            reverse('aiimport-answers', args=[task.id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {},
            },
            content_type='application/json',
        )

        task.refresh_from_db()
        if task.status == 'completed':
            self.assertIn('requests', task.generated_summary)
            self.assertGreater(len(task.generated_summary['requests']), 0)


# ============================================================================
# Preview tests
# ============================================================================

class TestAIImportPreview(AIImportViewTestBase):

    def test_preview_after_answers(self):
        """Preview should return generated requests list."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )

        self.client.post(
            reverse('aiimport-answers', args=[task_id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {},
            },
            content_type='application/json',
        )

        response = self.client.get(reverse('aiimport-preview', args=[task_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsInstance(data, list)


# ============================================================================
# Save tests
# ============================================================================

class TestAIImportSave(AIImportViewTestBase):

    def test_save_success(self):
        """Save should create ApiRequest records."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )

        self.client.post(
            reverse('aiimport-answers', args=[task_id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {},
            },
            content_type='application/json',
        )

        task = AIImportTask.objects.get(id=task_id)
        if task.status != 'completed':
            self.skipTest("Task did not complete (may need LLM config)")

        response = self.client.post(
            reverse('aiimport-save', args=[task_id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('requests_created', data)
        self.assertGreater(len(data['requests_created']), 0)
        self.assertIn('collections_created', data)

        # Verify ApiRequest records exist
        from apps.api_testing.models import ApiRequest
        self.assertEqual(
            ApiRequest.objects.filter(id__in=data['requests_created']).count(),
            len(data['requests_created']),
        )

    def test_save_no_project(self):
        """Save without configuring project should return 400."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        # Manually set to completed (simulating what answers would do)
        task = AIImportTask.objects.get(id=task_id)
        task.status = 'completed'
        task.generated_summary = {
            'requests': [{'name': 'test', 'method': 'GET', 'url': '/test'}],
        }
        task.save()

        response = self.client.post(
            reverse('aiimport-save', args=[task.id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('未关联项目', response.json()['error'])

    def test_save_not_completed(self):
        """Save task that is not completed should return 400."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        response = self.client.post(
            reverse('aiimport-save', args=[task_id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('未完成', response.json()['error'])

    def test_save_no_requests(self):
        """Save with empty generated_summary should return 400."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        AIImportTask.objects.filter(id=task_id).update(
            status='completed',
            generated_summary={},
        )

        response = self.client.post(
            reverse('aiimport-save', args=[task_id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_save_with_auto_structure_true(self):
        """Save with auto_structure=True should group requests by tags."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )

        self.client.post(
            reverse('aiimport-answers', args=[task_id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {},
            },
            content_type='application/json',
        )

        task = AIImportTask.objects.get(id=task_id)
        if task.status != 'completed':
            self.skipTest("Task did not complete")

        response = self.client.post(
            reverse('aiimport-save', args=[task_id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # With auto_structure, should create a 'users' collection
        users_collections = ApiCollection.objects.filter(
            project=project, name='users',
        )
        self.assertEqual(users_collections.count(), 1)

    def test_save_with_auto_structure_false(self):
        """Save with auto_structure=False should create a single collection."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': False},
            content_type='application/json',
        )

        self.client.post(
            reverse('aiimport-answers', args=[task_id]),
            {
                'user_answers': {
                    'q_1': 'https://api.example.com',
                    'auth': 'none',
                },
                'environment_vars': {},
            },
            content_type='application/json',
        )

        task = AIImportTask.objects.get(id=task_id)
        if task.status != 'completed':
            self.skipTest("Task did not complete")

        response = self.client.post(
            reverse('aiimport-save', args=[task_id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # Should create exactly 1 collection with "AI Import" prefix
        self.assertEqual(len(data['collections_created']), 1)
        collection = ApiCollection.objects.get(id=data['collections_created'][0])
        self.assertIn('AI Import', collection.name)


# ============================================================================
# Questions tests
# ============================================================================

class TestAIImportQuestions(AIImportViewTestBase):

    def test_questions_success(self):
        """GET /{id}/questions/ should return questions and summary."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']

        response = self.client.get(reverse('aiimport-questions', args=[task_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('questions', data)
        self.assertIn('classification_summary', data)
        self.assertEqual(data['classification_summary']['endpoint_count'], 3)
        self.assertGreater(data['classification_summary']['total_params'], 0)

    def test_questions_after_configure(self):
        """Questions should still be available after configure."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )

        response = self.client.get(reverse('aiimport-questions', args=[task_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ============================================================================
# List tasks tests
# ============================================================================

class TestAIImportListTasks(AIImportViewTestBase):

    def test_list_tasks(self):
        """GET /list_tasks/ should return the user's tasks."""
        self._upload_file()
        self._upload_file()

        response = self.client.get(reverse('aiimport-list-tasks'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # Response could be paginated (dict with results) or a list
        if isinstance(data, dict) and 'results' in data:
            results = data['results']
        else:
            results = data
        self.assertGreaterEqual(len(results), 2)
        for task_data in results:
            self.assertEqual(task_data['created_by_name'], self.user.username)

    def test_list_tasks_filter_by_project(self):
        """List tasks filtered by project_id."""
        upload_resp = self._upload_file()
        task_id = upload_resp.json()['id']
        project = self._create_project()

        self.client.post(
            reverse('aiimport-configure', args=[task_id]),
            {'project_id': project.id, 'auto_structure': True},
            content_type='application/json',
        )

        response = self.client.get(
            reverse('aiimport-list-tasks') + f'?project_id={project.id}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        if isinstance(data, dict) and 'results' in data:
            results = data['results']
        else:
            results = data
        self.assertGreaterEqual(len(results), 1)
        for task_data in results:
            self.assertEqual(task_data['project'], project.id)

    def test_list_tasks_empty(self):
        """List tasks when user has none."""
        response = self.client.get(reverse('aiimport-list-tasks'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        if isinstance(data, dict) and 'results' in data:
            results = data['results']
        else:
            results = data
        self.assertEqual(len(results), 0)

    def test_list_tasks_other_user_not_visible(self):
        """Tasks from other users should not be visible."""
        self._upload_file()

        # Create another user and upload their own task
        from django.contrib.auth import get_user_model
        User = get_user_model()
        other_user = User.objects.create_user(
            username='other_user', password='testpass123',
        )
        # Login as other user and upload
        self.client.force_login(other_user)
        self._upload_file()

        # Login back as original user
        self.client.force_login(self.user)
        response = self.client.get(reverse('aiimport-list-tasks'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        if isinstance(data, dict) and 'results' in data:
            results = data['results']
        else:
            results = data

        # Original user should see only their own task
        for task_data in results:
            self.assertEqual(task_data['created_by'], self.user.id)
