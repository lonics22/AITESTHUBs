import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.api_testing.models import AIImportTask

User = get_user_model()


class TestAgentStateEndpoint(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="agenttest", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)
        self.task = AIImportTask.objects.create(
            created_by=self.user,
            status="parsing",
            progress=10,
        )

    def test_agent_state_returns_messages(self):
        url = reverse("aiimport-agent-state", kwargs={"pk": self.task.pk})
        response = self.client.get(url)
        assert response.status_code == 200
        assert "messages" in response.data
        assert "status" in response.data

    def test_agent_reply_requires_auth(self):
        self.client.force_authenticate(user=None)
        url = reverse("aiimport-agent-reply", kwargs={"pk": self.task.pk})
        response = self.client.post(url, {"message": "hello"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestAgentReplyWithInvalidTask(TestCase):
    """Test agent_reply with missing task / invalid data."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="replytest", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)

    def test_agent_reply_invalid_data(self):
        """answers 字段必须是对象"""
        task = AIImportTask.objects.create(
            created_by=self.user,
            status="parsing",
            progress=10,
        )
        url = reverse("aiimport-agent-reply", kwargs={"pk": task.pk})
        response = self.client.post(url, {"answers": "not_a_dict"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_agent_reply_not_found(self):
        """请求不存在的任务应返回 404"""
        url = reverse("aiimport-agent-reply", kwargs={"pk": 99999})
        response = self.client.post(url, {"message": "hello"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_agent_state_not_found(self):
        """请求不存在的任务状态应返回 404"""
        url = reverse("aiimport-agent-state", kwargs={"pk": 99999})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAgentReplyWithRealAgent(TestCase):
    """Integration test with ImportAgent.resume()."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="realagent", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)
        self.task = AIImportTask.objects.create(
            created_by=self.user,
            status="parsing",
            progress=10,
            generated_summary={"agent_messages": []},
        )

