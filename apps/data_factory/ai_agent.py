"""AI 数据生成 Agent — LangGraph 状态机"""

import json
import logging
import re
import os
import asyncio
from typing import Dict, List, Any, TypedDict, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from apps.requirement_analysis.models import AIModelConfig, PromptConfig, AIModelService
from .ai_context import retrieve_project_context
from .ai_types import validate_and_fix_record

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Agent 运行时状态"""
    project_id: int
    field_defs: list
    api_info: dict
    user_inputs: dict
    classification: dict | None
    project_context: dict | None
    generated_data: list | None
    output_format: str
    count: int
    language: str
    error: str | None


def _get_active_config():
    """获取当前活跃的 data_generator 模型和提示词配置"""
    model_config = AIModelConfig.objects.filter(
        role='data_generator', is_active=True
    ).first()
    prompt_config = PromptConfig.objects.filter(
        prompt_type='data_generator', is_active=True
    ).first()
    return model_config, prompt_config


# ── 节点 1: 字段分类 ──────────────────────────────────────────

def classify_fields(state: AgentState) -> AgentState:
    """LLM 分析接口字段，返回 auto/manual/context_ref 分类"""
    model_config, _ = _get_active_config()
    if not model_config:
        state['error'] = '未配置 data_generator 模型'
        return state

    from django.conf import settings
    classify_prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_field_classify.md')
    try:
        with open(classify_prompt_path, 'r', encoding='utf-8') as f:
            classify_prompt = f.read()
    except FileNotFoundError:
        state['error'] = '字段分类提示词文件未找到'
        return state

    api_info_str = json.dumps(state['api_info'], ensure_ascii=False, indent=2)
    prompt = classify_prompt.replace('{{api_info}}', api_info_str)

    messages = [
        {"role": "system", "content": "你是一个接口测试专家，严格按格式输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
        content = response['choices'][0]['message']['content']
        json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            result = json.loads(content)
        state['classification'] = result
    except Exception as e:
        logger.error(f"字段分类失败: {e}")
        state['error'] = f"字段分类失败: {e}"

    return state


def route_after_classify(state: AgentState) -> Literal["wait_for_user", "retrieve_context", "generate_data"]:
    """分类后路由：有 manual 字段 → 等用户；无 manual 但 context_ref → 检索；否则直接生成"""
    if state['error']:
        return "generate_data"

    classification = state.get('classification', {})
    manual_fields = classification.get('manual_fields', [])
    context_fields = classification.get('context_fields', [])

    if manual_fields:
        return "wait_for_user"
    elif context_fields:
        return "retrieve_context"
    else:
        return "generate_data"


# ── 节点 2: 等待用户输入（interrupt） ─────────────────────────

def wait_for_user(state: AgentState) -> AgentState:
    """挂起 Agent，等待用户填写 manual 字段（通过 interrupt）"""
    classification = state.get('classification', {})
    manual_fields = classification.get('manual_fields', [])

    user_values = interrupt({
        "type": "manual_field_input",
        "fields": manual_fields,
        "api_info": state['api_info'],
    })

    if isinstance(user_values, dict):
        state['user_inputs'] = user_values

    return state


def route_after_wait(state: AgentState) -> Literal["retrieve_context", "generate_data"]:
    """用户填写完后的路由：还有 context_ref → 检索，否则直接生成"""
    classification = state.get('classification', {})
    if classification and classification.get('context_fields'):
        return "retrieve_context"
    return "generate_data"


# ── 节点 3: 检索项目上下文 ────────────────────────────────────

def retrieve_context_node(state: AgentState) -> AgentState:
    """从当前项目中检索可复用的测试数据"""
    context = retrieve_project_context(state['project_id'])
    state['project_context'] = context
    return state


# ── 节点 4: 生成数据（LLM + 后验修正） ────────────────────────

def generate_data(state: AgentState) -> AgentState:
    """LLM 生成最终测试数据，后验校验后返回"""
    model_config, prompt_config = _get_active_config()
    if not model_config or not prompt_config:
        state['error'] = '未配置 data_generator 模型或提示词'
        return state

    # 构建字段描述
    fields_str = "\n".join([
        f"{i+1}. 字段名：{f['name']}\n   - 类型：{f.get('type', 'string')}\n   - 描述：{f.get('description', '')}"
        for i, f in enumerate(state['field_defs'])
    ])

    # 构建上下文
    context_parts = []
    if state.get('user_inputs'):
        context_parts.append(f"用户指定值：\n{json.dumps(state['user_inputs'], ensure_ascii=False, indent=2)}")
    if state.get('project_context'):
        ctx = state['project_context']
        if ctx.get('existing_usernames'):
            context_parts.append(f"已有用户名：{', '.join(ctx['existing_usernames'])}")
        if ctx.get('available_ids'):
            context_parts.append(f"可用ID：{', '.join(ctx['available_ids'])}")

    context_str = "\n\n".join(context_parts) if context_parts else "无额外上下文"

    prompt_content = prompt_config.content
    prompt_content = prompt_content.replace('{{fields}}', fields_str)
    prompt_content = prompt_content.replace('{{count}}', str(state.get('count', 5)))
    prompt_content = prompt_content.replace('{{data_format}}', state.get('output_format', 'json'))
    prompt_content = prompt_content.replace('{{language}}', state.get('language', '中文'))
    prompt_content = prompt_content.replace('{{context}}', context_str)

    result_format = f"\n```{state.get('output_format', 'json')}\n\n```\n"
    prompt_content = prompt_content.replace('{{result_format}}', result_format)

    messages = [
        {"role": "system", "content": "你是一个测试数据生成专家，严格按格式输出。"},
        {"role": "user", "content": prompt_content},
    ]

    try:
        response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
        content = response['choices'][0]['message']['content']

        # 解析输出
        if state.get('output_format') == 'json':
            json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                raw_data = json.loads(json_match.group(1))
            else:
                raw_data = json.loads(content)

            # 单条记录转列表
            if isinstance(raw_data, dict):
                raw_data = [raw_data]

            # 后验修正
            validated = []
            for record in raw_data:
                fixed = validate_and_fix_record(record, state['field_defs'])
                validated.append(fixed)
            state['generated_data'] = validated
        else:
            state['generated_data'] = [{"raw": content}]
    except Exception as e:
        logger.error(f"数据生成失败: {e}")
        state['error'] = f"数据生成失败: {e}"

    return state


# ── 构建编译图 ────────────────────────────────────────────────

def build_agent() -> StateGraph:
    """构建并编译 LangGraph Agent"""
    builder = StateGraph(AgentState)

    builder.add_node("classify_fields", classify_fields)
    builder.add_node("wait_for_user", wait_for_user)
    builder.add_node("retrieve_context", retrieve_context_node)
    builder.add_node("generate_data", generate_data)

    builder.add_edge(START, "classify_fields")
    builder.add_conditional_edges(
        "classify_fields",
        route_after_classify,
    )
    builder.add_conditional_edges(
        "wait_for_user",
        route_after_wait,
    )
    builder.add_edge("retrieve_context", "generate_data")
    builder.add_edge("generate_data", END)

    return builder.compile(checkpointer=InMemorySaver())


# ── 对外接口 ──────────────────────────────────────────────────

async def run_agent(
    project_id: int,
    field_defs: list,
    api_info: dict,
    user_inputs: dict = None,
    count: int = 5,
    output_format: str = 'json',
    language: str = '中文',
) -> dict:
    """运行数据生成 Agent"""
    agent = build_agent()

    initial_state: AgentState = {
        "project_id": project_id,
        "field_defs": field_defs,
        "api_info": api_info,
        "user_inputs": user_inputs or {},
        "classification": None,
        "project_context": None,
        "generated_data": None,
        "output_format": output_format,
        "count": count,
        "language": language,
        "error": None,
    }

    result = await agent.ainvoke(initial_state, {"configurable": {"thread_id": "data-gen-1"}})
    return result
