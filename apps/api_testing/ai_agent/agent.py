"""LangGraph StateGraph 驱动的主 Agent"""
from __future__ import annotations

import logging
from typing import List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from apps.api_testing.ai_agent.tools import (
    parse_document_tool,
    classify_parameters_tool,
    generate_test_cases_tool,
    save_to_database_tool,
)
from apps.api_testing.ai_agent.prompts import (
    ENDPOINT_GENERATION_PROMPT,
    OUTPUT_SCHEMA_CONSTRAINT,
)
from apps.api_testing.ai_agent.persistence import DjangoCheckpointSaver

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Agent 运行时状态"""
    task_id: int
    status: str
    parsed_endpoints: List[dict]
    classification: dict
    generated_requests: List[dict]
    user_questions: List[dict]
    user_answers: dict
    messages: List[dict]
    error: Optional[str]
    progress: int


# ---- Graph 节点函数 ----

def parse_document_node(state: AgentState) -> dict:
    """解析文档节点"""
    task_id = state["task_id"]
    from apps.api_testing.models import AIImportTask

    try:
        task = AIImportTask.objects.get(id=task_id)
        raw_content = task.raw_content
    except AIImportTask.DoesNotExist:
        return {"status": "failed", "error": f"Task {task_id} not found"}

    try:
        endpoints = parse_document_tool.invoke({"raw_content": raw_content})
        return {
            "status": "parsed",
            "parsed_endpoints": endpoints,
            "progress": 30,
            "messages": state.get("messages", []) + [
                {"role": "agent", "content": f"已解析文档，提取到 {len(endpoints)} 个端点"}
            ],
        }
    except Exception as e:
        logger.exception("parse_document_node failed")
        return {"status": "failed", "error": str(e)}


def classify_node(state: AgentState) -> dict:
    """参数分类节点"""
    endpoints = state.get("parsed_endpoints", [])
    if not endpoints:
        return {"status": "failed", "error": "No endpoints to classify"}

    try:
        classification = classify_parameters_tool.invoke({"endpoints": endpoints})
        auto_count = classification.get("auto_params", 0)
        manual_count = classification.get("manual_params", 0)

        msg = f"参数分析完成: {auto_count} 个可自动生成, {manual_count} 个需要用户确认"
        if manual_count > 0:
            msg += "，请提供以下参数的值"

        return {
            "status": "classified",
            "classification": classification,
            "progress": 50,
            "messages": state.get("messages", []) + [
                {"role": "agent", "content": msg}
            ],
        }
    except Exception as e:
        logger.exception("classify_node failed")
        return {"status": "failed", "error": str(e)}


def generate_node(state: AgentState) -> dict:
    """用例生成节点"""
    endpoints = state.get("parsed_endpoints", [])
    classification = state.get("classification", {})
    user_answers = state.get("user_answers", {})

    if not endpoints:
        return {"status": "failed", "error": "No endpoints to generate"}

    try:
        raw_requests = generate_test_cases_tool.invoke({
            "endpoints": endpoints,
            "classification": classification,
            "user_answers": user_answers,
            "tester_prompt": "",  # Placeholder — P1 will integrate actual prompt
        })
        return {
            "status": "generated",
            "generated_requests": raw_requests,
            "progress": 80,
            "messages": state.get("messages", []) + [
                {"role": "agent", "content": f"已生成 {len(raw_requests)} 个测试用例"}
            ],
        }
    except Exception as e:
        logger.exception("generate_node failed")
        return {"status": "failed", "error": str(e)}


def save_node(state: AgentState) -> dict:
    """保存节点"""
    requests = state.get("generated_requests", [])
    if not requests:
        return {"status": "failed", "error": "No requests to save"}

    from apps.api_testing.models import AIImportTask

    try:
        task = AIImportTask.objects.get(id=state["task_id"])
        result = save_to_database_tool.invoke({
            "requests": requests,
            "project_id": task.project_id,
            "collection_id": None,
            "auto_structure": True,
            "user_id": task.created_by_id,
        })
        return {
            "status": "completed",
            "progress": 100,
            "messages": state.get("messages", []) + [
                {
                    "role": "agent",
                    "content": (
                        f"保存完成！创建了 {len(result.get('collections_created', []))} 个集合，"
                        f"{len(result.get('requests_created', []))} 个请求"
                    ),
                }
            ],
        }
    except Exception as e:
        logger.exception("save_node failed")
        return {"status": "failed", "error": str(e)}


def error_node(state: AgentState) -> dict:
    """错误处理节点"""
    error_msg = state.get("error", "Unknown error")
    logger.error("Agent error: %s", error_msg)
    return {
        "status": "failed",
        "error": error_msg,
        "messages": state.get("messages", []) + [
            {"role": "agent", "content": f"处理失败: {error_msg}"}
        ],
    }


# ---- Router 函数 ----

def router_after_parse(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "error"
    return "classify"


def router_after_classify(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "error"
    return "generate"


def router_after_generate(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "error"
    return "save"


def router_after_save(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "error"
    return END


# ---- Graph 构建 ----

def build_agent_graph() -> StateGraph:
    """构建并返回 Agent StateGraph"""
    workflow = StateGraph(AgentState)

    workflow.add_node("parse", parse_document_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("save", save_node)
    workflow.add_node("error", error_node)

    workflow.set_entry_point("parse")

    workflow.add_conditional_edges("parse", router_after_parse, {
        "classify": "classify",
        "error": "error",
    })
    workflow.add_conditional_edges("classify", router_after_classify, {
        "generate": "generate",
        "error": "error",
    })
    workflow.add_conditional_edges("generate", router_after_generate, {
        "save": "save",
        "error": "error",
    })
    workflow.add_conditional_edges("save", router_after_save, {
        END: END,
        "error": "error",
    })
    workflow.add_edge("error", END)

    return workflow


class ImportAgent:
    """AI 导入 Agent 主类"""

    def __init__(self, task_id: int):
        self.task_id = task_id
        self.checkpointer = DjangoCheckpointSaver()
        self.graph = build_agent_graph().compile(checkpointer=self.checkpointer)

    def run(self) -> AgentState:
        """执行完整的 Agent 流程"""
        initial_state: AgentState = {
            "task_id": self.task_id,
            "status": "starting",
            "parsed_endpoints": [],
            "classification": {},
            "generated_requests": [],
            "user_questions": [],
            "user_answers": {},
            "messages": [],
            "error": None,
            "progress": 0,
        }

        config = {"configurable": {"task_id": self.task_id}}
        result = self.graph.invoke(initial_state, config)
        return self._update_task(result)

    def resume(self, user_message: str, user_answers: dict) -> AgentState:
        """用户回复后恢复 Agent 执行"""
        from apps.api_testing.models import AIImportTask

        # 从 checkpoint 恢复，无 checkpoint 时使用默认初始状态
        config = {"configurable": {"task_id": self.task_id}}
        saved_state = self.checkpointer.get(config)
        if saved_state is None:
            saved_state = {
                "task_id": self.task_id,
                "status": "starting",
                "parsed_endpoints": [],
                "classification": {},
                "generated_requests": [],
                "user_questions": [],
                "user_answers": {},
                "messages": [],
                "error": None,
                "progress": 0,
            }

        saved_state["user_answers"] = user_answers
        saved_state["messages"] = saved_state.get("messages", []) + [
            {"role": "user", "content": user_message}
        ]

        result = self.graph.invoke(
            saved_state, config,
        )
        return self._update_task(result)

    def _update_task(self, state: AgentState) -> AgentState:
        """将 Agent 状态同步到 AIImportTask"""
        from apps.api_testing.models import AIImportTask

        try:
            task = AIImportTask.objects.get(id=self.task_id)
            updates = []
            if state.get("parsed_endpoints"):
                task.parsed_endpoints = state["parsed_endpoints"]
                updates.append("parsed_endpoints")
            if state.get("classification"):
                task.ai_classification = state["classification"]
                updates.append("ai_classification")
            if state.get("generated_requests"):
                summary = task.generated_summary or {}
                summary["requests"] = state["generated_requests"]
                task.generated_summary = summary
                updates.append("generated_summary")
            if state.get("error"):
                task.error_message = state["error"]
                updates.append("error_message")
            task.status = state.get("status", "failed")
            updates.append("status")
            task.progress = state.get("progress", 0)
            updates.append("progress")
            task.save(update_fields=updates)
        except AIImportTask.DoesNotExist:
            logger.warning("ImportAgent: task %s not found for state sync", self.task_id)

        return state
