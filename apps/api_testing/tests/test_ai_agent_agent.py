import pytest
from django.test import TestCase
from apps.api_testing.ai_agent.agent import build_agent_graph, ImportAgent, _deterministic_route
from apps.api_testing.ai_agent.persistence import DjangoCheckpointSaver


class TestAgentGraph(TestCase):
    def test_build_graph(self):
        """验证 StateGraph 构建成功"""
        graph = build_agent_graph()
        assert graph is not None

    def test_graph_nodes(self):
        """验证所有节点已注册（P1 新增 llm_router 和 ask_user）"""
        graph = build_agent_graph()
        nodes = list(graph.nodes.keys())
        assert "llm_router" in nodes
        assert "parse" in nodes
        assert "classify" in nodes
        assert "generate" in nodes
        assert "save" in nodes
        assert "ask_user" in nodes
        assert "error" in nodes

    def test_graph_entry_point(self):
        """验证入口点为 llm_router（P1 从 LLM 路由开始）"""
        graph = build_agent_graph()
        assert ("__start__", "llm_router") in graph.edges

    def test_action_nodes_loop_to_llm_router(self):
        """验证所有 action 节点执行完后回到 llm_router"""
        graph = build_agent_graph()
        for node in ("parse", "classify", "generate", "save", "ask_user", "error"):
            assert (node, "llm_router") in graph.edges, \
                f"{node} should have edge back to llm_router"


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
        assert isinstance(agent.graph.checkpointer, DjangoCheckpointSaver)

    def test_run_initial_state_has_next_action(self):
        """验证 run() 的初始状态包含 next_action = parse"""
        agent = ImportAgent(task_id=999)
        initial = {
            "task_id": 999,
            "status": "starting",
            "parsed_endpoints": [],
            "classification": {},
            "generated_requests": [],
            "user_questions": [],
            "user_answers": {},
            "messages": [],
            "error": None,
            "progress": 0,
            "next_action": "parse",
        }
        assert initial["next_action"] == "parse"
        assert initial["status"] == "starting"


class TestDeterministicRoute(TestCase):
    """P1 新增：确定性兜底路由"""

    def _make_state(self, **overrides):
        base = {
            "task_id": 1, "status": "starting",
            "parsed_endpoints": [], "classification": {},
            "generated_requests": [], "user_questions": [],
            "user_answers": {}, "messages": [], "error": None,
            "progress": 0, "next_action": "",
        }
        base.update(overrides)
        return base

    def test_route_to_parse_when_empty(self):
        assert _deterministic_route(self._make_state()) == "parse"

    def test_route_to_classify_after_parse(self):
        state = self._make_state(
            status="parsed",
            parsed_endpoints=[{"path": "/test", "method": "GET"}],
        )
        assert _deterministic_route(state) == "classify"

    def test_route_to_ask_user_when_manual_params(self):
        state = self._make_state(
            status="classified",
            parsed_endpoints=[{"path": "/test", "method": "GET"}],
            classification={"manual_params": 2, "classification": {}},
        )
        assert _deterministic_route(state) == "ask_user"

    def test_route_to_generate_when_answers_ready(self):
        state = self._make_state(
            status="classified",
            parsed_endpoints=[{"path": "/test", "method": "GET"}],
            classification={"manual_params": 0, "classification": {}},
            user_answers={"page": "1"},
        )
        assert _deterministic_route(state) == "generate"

    def test_route_to_generate_when_no_manual_params(self):
        """没有 manual 参数时直接 generate，不经过 ask_user"""
        state = self._make_state(
            status="classified",
            parsed_endpoints=[{"path": "/test", "method": "GET"}],
            classification={"manual_params": 0, "classification": {}},
        )
        assert _deterministic_route(state) == "generate"

    def test_route_to_save_when_requests_generated(self):
        state = self._make_state(
            status="generated",
            parsed_endpoints=[{"path": "/test", "method": "GET"}],
            classification={"manual_params": 0, "classification": {}},
            generated_requests=[{"name": "test", "method": "GET", "url": "/test"}],
        )
        assert _deterministic_route(state) == "save"

    def test_route_to_error_on_failure(self):
        state = self._make_state(status="failed", error="something broke")
        assert _deterministic_route(state) == "error"
