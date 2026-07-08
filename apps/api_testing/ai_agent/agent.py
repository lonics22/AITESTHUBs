"""LangGraph StateGraph 驱动的主 Agent — P1 LLM 驱动路由"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from apps.api_testing.ai_agent.tools import (
    parse_document_tool,
    classify_parameters_tool,
    generate_test_cases_tool,
    save_to_database_tool,
)
from apps.api_testing.ai_agent.prompts import (
    AGENT_SYSTEM_PROMPT,
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
    next_action: str              # LLM 决策结果


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def _call_llm(messages: List[dict]) -> str:
    """同步调用 LLM 返回文本回复"""
    from apps.requirement_analysis.models import AIModelConfig, AIModelService

    config = AIModelConfig.objects.filter(is_active=True).first()
    if not config:
        raise RuntimeError("No active AIModelConfig found")

    response = asyncio.run(
        AIModelService.call_openai_compatible_api(config, messages)
    )
    return response["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 状态格式化（给 LLM 看的上下文）
# ---------------------------------------------------------------------------

def _format_state_for_llm(state: AgentState) -> str:
    """将当前状态格式化为 LLM 可读的文本"""
    sections = []

    sections.append(f"## 当前状态")
    sections.append(f"- status: {state.get('status', 'unknown')}")
    sections.append(f"- progress: {state.get('progress', 0)}%")
    sections.append(f"- parsed_endpoints: {len(state.get('parsed_endpoints', []))} 个")
    sections.append(f"- classification: {'已完成' if state.get('classification') else '未开始'}")
    sections.append(f"- generated_requests: {len(state.get('generated_requests', []))} 个")
    sections.append(f"- user_answers: {'已提供' if state.get('user_answers') else '无'}")

    if state.get("classification"):
        c = state["classification"]
        sections.append(f"\n### 参数分类摘要")
        sections.append(f"- 自动生成: {c.get('auto_params', 0)}")
        sections.append(f"- 需用户确认: {c.get('manual_params', 0)}")
        sections.append(f"- 上下文引用: {c.get('context_ref_params', 0)}")

    sections.append(f"\n### 最近对话")
    for msg in state.get("messages", [])[-6:]:
        role = "🤖" if msg["role"] == "agent" else "👤"
        content = msg.get("content", "")[:200]
        sections.append(f"{role}: {content}")

    return "\n".join(sections)


def _format_endpoint_prompt(endpoints: List[dict], user_answers: dict) -> str:
    """为每个端点生成 LLM 用例生成提示词"""
    prompts = []
    for ep in endpoints:
        params_str = json.dumps(ep.get("parameters", []), ensure_ascii=False, indent=2)
        prompts.append(ENDPOINT_GENERATION_PROMPT.format(
            method=ep.get("method", "GET"),
            path=ep.get("path", ""),
            summary=ep.get("summary", ""),
            description=ep.get("description", ""),
            parameters=params_str,
            output_schema=OUTPUT_SCHEMA_CONSTRAINT,
        ))
    return "\n---\n".join(prompts)


# ---------------------------------------------------------------------------
# 确定性兜底路由（LLM 不可用时）
# ---------------------------------------------------------------------------

def _deterministic_route(state: AgentState) -> str:
    """当 LLM 不可用时，根据状态做确定性决策"""
    if state.get("status") == "failed":
        return "error"
    if not state.get("parsed_endpoints"):
        return "parse"
    if not state.get("classification"):
        return "classify"
    if (state.get("classification", {}).get("manual_params", 0) > 0
            and not state.get("user_answers")):
        return "ask_user"
    if not state.get("generated_requests"):
        return "generate"
    if state.get("status") != "completed":
        return "save"
    return END


# ---------------------------------------------------------------------------
# LLM 路由节点 — 决定下一步执行什么操作
# ---------------------------------------------------------------------------

def llm_router_node(state: AgentState) -> dict:
    """调用 LLM 决定下一步操作，结果写入 state['next_action']"""
    # 终态直接结束
    if state.get("status") in ("completed", "failed"):
        return {"next_action": END}

    # 等待用户回复 — 检查是否有新 answers
    if state.get("status") == "waiting_user":
        if state.get("user_answers"):
            # 用户已回答，继续
            pass
        else:
            # 继续等待
            return {"next_action": END}

    system_msg = AGENT_SYSTEM_PROMPT
    user_msg = _format_state_for_llm(state)

    try:
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        response = _call_llm(messages)

        # 解析 LLM 决策
        decision = json.loads(response.strip())
        action = decision.get("next", "").strip()

        # 校验 action
        valid_actions = {"parse", "classify", "generate", "save", "ask_user", END}
        if action not in valid_actions:
            logger.warning("LLM returned invalid action: %s", action)
            action = _deterministic_route(state)

        logger.info("LLM router decision: %s (reason: %s)", action, decision.get("reasoning", ""))
        return {"next_action": action}

    except Exception as e:
        logger.warning("LLM routing failed (%s), using deterministic fallback", e)
        return {"next_action": _deterministic_route(state)}


# ---------------------------------------------------------------------------
# 操作节点
# ---------------------------------------------------------------------------

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
            msg += "，请问你是否要提供这些参数的值？如果不提供，我会自动使用默认值生成测试用例。"

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


def ask_user_node(state: AgentState) -> dict:
    """向用户提问 — 生成自然语言问题让用户确认参数"""
    classification = state.get("classification", {})
    endpoints = state.get("parsed_endpoints", [])

    # 收集 manual 参数
    manual_params: List[dict] = []
    class_map = classification.get("classification", classification)
    for ep in endpoints:
        key = f"{ep.get('method', 'GET').upper()} {ep.get('path', '')}"
        classified = class_map.get(key, {})
        for p in classified.get("manual", []):
            manual_params.append({
                "endpoint": key,
                "name": p.get("name", ""),
                "location": p.get("location", "query"),
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            })

    if not manual_params:
        return {"status": "classified", "user_questions": [], "messages": state.get("messages", [])}

    # 尝试用 LLM 生成自然的提问
    try:
        import json as _json
        params_json = _json.dumps(manual_params, ensure_ascii=False, indent=2)
        prompt = f"""根据以下需要用户提供的参数列表，生成简洁的提问。每个提问用一句话解释需要什么值。

参数：
{params_json}

按以下 JSON 格式返回（不要用 markdown）：
[{{"question": "请提供用户 ID 的值", "param_name": "user_id"}}]
"""
        messages = [
            {"role": "system", "content": "你是 API 测试助手，向用户询问参数值。使用中文提问。"},
            {"role": "user", "content": prompt},
        ]
        response = _call_llm(messages)
        questions = _json.loads(response.strip())
    except Exception:
        # 兜底：生成简单提问
        questions = [
            {"question": f"请提供参数「{p['name']}」的值（{p.get('description', p['type'])}，位于 {p['location']}）",
             "param_name": p["name"]}
            for p in manual_params
        ]

    question_text = "\n".join(f"- {q['question']}" for q in questions)
    return {
        "status": "waiting_user",
        "user_questions": questions,
        "messages": state.get("messages", []) + [
            {"role": "agent", "content": f"需要你确认以下参数：\n{question_text}\n\n请回复提供这些参数的值，或回复「跳过」使用默认值。"}
        ],
    }


def generate_node(state: AgentState) -> dict:
    """用例生成节点 — 使用 tester.md 提示词规范生成"""
    endpoints = state.get("parsed_endpoints", [])
    classification = state.get("classification", {})
    user_answers = state.get("user_answers", {})

    if not endpoints:
        return {"status": "failed", "error": "No endpoints to generate"}

    try:
        # 构建 endpoints 维度的提示词（供后续 P2 LLM 生成使用）
        ep_prompts = _format_endpoint_prompt(endpoints, user_answers)
        logger.info("generate_node: endpoint prompts built (%d chars)", len(ep_prompts))

        raw_requests = generate_test_cases_tool.invoke({
            "endpoints": endpoints,
            "classification": classification,
            "user_answers": user_answers,
            "tester_prompt": ep_prompts,
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


# ---------------------------------------------------------------------------
# Graph 构建
# ---------------------------------------------------------------------------

def build_agent_graph() -> StateGraph:
    """构建 LLM 驱动的 Agent StateGraph

    结构: llm_router → [action node] → llm_router → ... → END
    LLM 每次在 router 节点决定下一步执行哪个 action。
    """
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("llm_router", llm_router_node)
    workflow.add_node("parse", parse_document_node)
    workflow.add_node("classify", classify_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("save", save_node)
    workflow.add_node("ask_user", ask_user_node)
    workflow.add_node("error", error_node)

    workflow.set_entry_point("llm_router")

    # 所有 action 节点执行完后回到 llm_router 做下一步决策
    for node in ("parse", "classify", "generate", "save", "ask_user", "error"):
        workflow.add_edge(node, "llm_router")

    # LLM router 根据 next_action 决定去向
    workflow.add_conditional_edges(
        "llm_router",
        lambda state: state.get("next_action", END),
        {
            "parse": "parse",
            "classify": "classify",
            "generate": "generate",
            "save": "save",
            "ask_user": "ask_user",
            "error": "error",
            END: END,
        },
    )

    return workflow


# ---------------------------------------------------------------------------
# ImportAgent 主类
# ---------------------------------------------------------------------------

class ImportAgent:
    """AI 导入 Agent 主类 — LLM 驱动路由"""

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
            "next_action": "parse",
        }

        config = {"configurable": {"task_id": self.task_id}}
        result = self.graph.invoke(initial_state, config)
        return self._update_task(result)

    def resume(self, user_message: str, user_answers: dict) -> AgentState:
        """用户回复后恢复 Agent 执行"""
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
                "next_action": "parse",
            }

        # 注入用户回复
        saved_state["user_answers"] = user_answers
        saved_state["status"] = "classified"  # 用户已回答，解除 waiting
        saved_state["messages"] = saved_state.get("messages", []) + [
            {"role": "user", "content": user_message}
        ]
        # 清空 next_action 让 LLM 重新决策
        saved_state["next_action"] = ""

        result = self.graph.invoke(saved_state, config)
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
            if state.get("user_questions"):
                summary = task.generated_summary or {}
                summary["user_questions"] = state["user_questions"]
                task.generated_summary = summary
                updates.append("generated_summary")
            task.status = state.get("status", "failed")
            updates.append("status")
            task.progress = state.get("progress", 0)
            updates.append("progress")
            task.save(update_fields=updates)
        except AIImportTask.DoesNotExist:
            logger.warning("ImportAgent: task %s not found for state sync", self.task_id)

        return state
