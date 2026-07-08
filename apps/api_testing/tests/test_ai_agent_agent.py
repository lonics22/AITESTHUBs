import pytest
from django.test import TestCase
from apps.api_testing.ai_agent.agent import build_agent_graph, ImportAgent


class TestAgentGraph(TestCase):
    def test_build_graph(self):
        """验证 StateGraph 构建成功"""
        graph = build_agent_graph()
        assert graph is not None

    def test_graph_nodes(self):
        """验证所有节点已注册"""
        graph = build_agent_graph()
        nodes = list(graph.nodes.keys())
        assert "parse" in nodes
        assert "classify" in nodes
        assert "generate" in nodes
        assert "save" in nodes
        assert "error" in nodes

    def test_graph_entry_point(self):
        """验证入口点为 parse"""
        graph = build_agent_graph()
        # langgraph v1.2.6 将入口点存储为从 __start__ 到目标节点的边
        assert ("__start__", "parse") in graph.edges


class TestImportAgent(TestCase):
    def test_import_agent_init(self):
        """验证 ImportAgent 初始化"""
        agent = ImportAgent(task_id=999)
        assert agent.task_id == 999
        assert agent.graph is not None
        assert agent.checkpointer is not None

    def test_compiled_graph_has_checkpointer(self):
        """验证编译后的 graph 正确绑定了 checkpointer"""
        agent = ImportAgent(task_id=999)
        from apps.api_testing.ai_agent.persistence import DjangoCheckpointSaver
        assert isinstance(agent.graph.checkpointer, DjangoCheckpointSaver)
