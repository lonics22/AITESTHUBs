# AI 导入 Agent 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangChain + LangGraph 的 AI Agent 架构替换当前三阶段硬编码 Pipeline，实现接口文档上传后由 Agent 自主理解、分类、提问、生成并保存测试用例

**Architecture:** `apps/api_testing/ai_agent/` 包下 5 个模块（agent.py, tools.py, prompts.py, schema.py, persistence.py）+ 后端 API 端点（agent-reply/agent-state）+ 前端对话组件。Agent 通过 LangGraph StateGraph 驱动，LLM 调用复用现有 `AIModelService`

**Tech Stack:** LangGraph 1.2.6（已安装）, LangChain-core 1.2.6（已安装）, Pydantic 2.12.5（已安装）, Django 4.2

## Global Constraints

- 复用现有 `AIImportTask` 模型字段（`parsed_endpoints`, `ai_classification`, `generated_summary` JSONFields），不新建表
- 复用现有的 `AIModelService.call_openai_compatible_api()`（async 方法，用 `asyncio.run()` 桥接）和 `AIModelConfig`
- LLM 输出必须经过 `validate_llm_output()` Pydantic 校验层，不符合时触发 Agent 重试
- 生成的用例存入现有的 `ApiCollection` / `ApiRequest` 模型
- 前端保留 Step 0（上传）和 Step 5（结果），Step 2-4 合并为对话式界面
- `apps/api_testing/ai_agent/` 包路径使用 `apps.api_testing.ai_agent`
- 现有 `ai_import_service.py` 保留不动，作为兜底策略

---
## File Structure

| 文件 | 角色 |
|------|------|
| `apps/api_testing/ai_agent/__init__.py` | 包初始化，导出 `ImportAgent` |
| `apps/api_testing/ai_agent/schema.py` | Pydantic 模型 `ApiRequestSchema` + `validate_llm_output()` |
| `apps/api_testing/ai_agent/prompts.py` | Agent 系统提示词 + 用例生成提示词 |
| `apps/api_testing/ai_agent/persistence.py` | `DjangoCheckpointSaver` — Agent 状态持久化 |
| `apps/api_testing/ai_agent/tools.py` | 4 个 `@tool`：parse/classify/generate/save |
| `apps/api_testing/ai_agent/agent.py` | LangGraph StateGraph 定义 + `ImportAgent` 类 |
| `apps/api_testing/views.py` | 新增 `agent_state` + `agent_reply` action |
| `apps/api_testing/serializers.py` | 新增 `AgentReplySerializer` |
| `apps/api_testing/urls.py` | 注册新端点（由 DRF router 自动处理） |
| `frontend/src/api/api-testing-import.js` | 新增 `getAgentState()` + `sendAgentReply()` |
| `frontend/src/views/api-testing/AIImportWizard.vue` | Step 2 改为 Agent 对话界面 |

---

### Task 1: Schema 验证模块 + Prompt 模板

**Files:**
- Create: `apps/api_testing/ai_agent/__init__.py`
- Create: `apps/api_testing/ai_agent/schema.py`
- Create: `apps/api_testing/ai_agent/prompts.py`
- Test: `apps/api_testing/tests/test_ai_agent_schema.py`

**Interfaces:**
- Produces: `validate_llm_output(raw: List[dict]) -> List[dict]` — 校验 LLM 输出，返回清理后的 dict 列表，失败抛 `ValueError`
- Produces: `AGENT_SYSTEM_PROMPT` — Agent 系统提示词字符串
- Produces: `ENDPOINT_GENERATION_PROMPT` — 端点生成提示词模板（`.format()` 接口）
- Produces: `OUTPUT_SCHEMA_CONSTRAINT` — JSON 格式约束文本，嵌入到生成提示词末尾

- [ ] **Step 1: 创建包初始化文件**

```python
# apps/api_testing/ai_agent/__init__.py
```

空文件即可（或简单 docstring）。

- [ ] **Step 2: 写入 schema.py**

```python
# apps/api_testing/ai_agent/schema.py
"""LLM 输出 Schema 强制校验层"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_HTTP_METHODS = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
})


class ApiRequestSchema(BaseModel):
    """LLM 生成的单条 API 请求，字段与 ApiRequest model 保持一致"""
    name: str = Field(..., max_length=200, description="请求名称")
    description: str = Field("", description="请求描述")
    method: str = Field("GET", description="请求方法")
    url: str = Field(..., description="请求 URL")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    params: Dict[str, str] = Field(default_factory=dict, description="URL 查询参数")
    body: Dict[str, Any] = Field(default_factory=dict, description="请求体")
    auth: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "none"}, description="认证信息"
    )
    assertions: List[Dict[str, Any]] = Field(default_factory=list, description="断言列表")
    pre_request_script: str = Field("", description="请求前脚本")
    post_request_script: str = Field("", description="请求后脚本")


class BatchApiRequestsSchema(BaseModel):
    """LLM 输出的完整批次"""
    requests: List[ApiRequestSchema]
    total: int


def validate_llm_output(raw: List[dict]) -> List[dict]:
    """校验 LLM 输出的每一条记录，确保字段类型和必填项都符合 ApiRequest 模型。

    Args:
        raw: LLM 返回的原始 JSON 解析后的 list[dict]

    Returns:
        清理后的 list[dict]，可直接传给 ApiRequest.objects.create()

    Raises:
        ValueError: 包含具体字段错误信息
    """
    batch = BatchApiRequestsSchema(requests=raw, total=len(raw))
    for i, req in enumerate(batch.requests):
        if req.method not in VALID_HTTP_METHODS:
            raise ValueError(
                f"请求 #{i} ('{req.name}'): method '{req.method}' 不是合法的 HTTP 方法"
            )
        if not req.url:
            raise ValueError(f"请求 #{i} ('{req.name}'): url 不能为空")
        if not req.name:
            raise ValueError(f"请求 #{i}: name 不能为空")
        if not isinstance(req.headers, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): headers 必须是键值对对象")
        if not isinstance(req.params, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): params 必须是键值对对象")
        if not isinstance(req.body, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): body 必须是对象")
        if not isinstance(req.auth, dict) or "type" not in req.auth:
            raise ValueError(f"请求 #{i} ('{req.name}'): auth 必须有 type 字段")

    logger.info("validate_llm_output: %d requests passed validation", len(batch.requests))
    return [req.dict(exclude_none=True) for req in batch.requests]
```

- [ ] **Step 3: 编写 schema 测试**

```python
# apps/api_testing/tests/test_ai_agent_schema.py
import pytest
from apps.api_testing.ai_agent.schema import validate_llm_output


class TestValidateLLMOutput:
    def test_valid_requests(self):
        raw = [
            {
                "name": "Get Users",
                "method": "GET",
                "url": "/api/users",
                "headers": {},
                "params": {"page": "1"},
                "body": {},
                "auth": {"type": "none"},
                "assertions": [],
                "pre_request_script": "",
                "post_request_script": "",
            }
        ]
        result = validate_llm_output(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Get Users"

    def test_invalid_method(self):
        raw = [{"name": "Bad", "method": "INVALID", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="不是合法的 HTTP 方法"):
            validate_llm_output(raw)

    def test_empty_url(self):
        raw = [{"name": "No URL", "method": "GET", "url": "", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="url 不能为空"):
            validate_llm_output(raw)

    def test_empty_name(self):
        raw = [{"name": "", "method": "GET", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="name 不能为空"):
            validate_llm_output(raw)

    def test_auth_missing_type(self):
        raw = [{"name": "X", "method": "GET", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"token": "abc"}, "assertions": []}]
        with pytest.raises(ValueError, match="auth 必须有 type 字段"):
            validate_llm_output(raw)

    def test_headers_not_dict(self):
        raw = [{"name": "X", "method": "GET", "url": "/test", "headers": "invalid", "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="headers 必须是键值对对象"):
            validate_llm_output(raw)

    def test_minimal_fields_get_defaults(self):
        """只有必填字段时，其他字段应填入合理的默认值"""
        raw = [{"name": "Minimal", "method": "POST", "url": "/api"}]
        result = validate_llm_output(raw)
        assert result[0]["method"] == "POST"
        assert result[0]["headers"] == {}
        assert result[0]["auth"] == {"type": "none"}
        assert result[0]["assertions"] == []
```

- [ ] **Step 4: 运行 schema 测试**

Run: `cd d:/AI/testhub && python -m pytest apps/api_testing/tests/test_ai_agent_schema.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: 写入 prompts.py**

```python
# apps/api_testing/ai_agent/prompts.py
"""Agent 提示词系统"""

AGENT_SYSTEM_PROMPT = """你是一个 API 测试用例生成专家。你的任务是根据用户上传的 API 文档，
与用户协作生成高质量的 API 测试用例。

## 你的能力
1. **理解 API 文档** — 解析 Swagger/OpenAPI/Postman/HAR 等格式
2. **参数分类** — 识别哪些参数可以自动生成、哪些需要用户确认
3. **用例生成** — 使用提供的测试用例规范生成完整的测试用例
4. **交互确认** — 对不确定的信息主动向用户提问

## 工作流程
1. 用户上传文档后，先解析提取所有端点
2. 分析每个端点的参数，决定哪些可以自动填充
3. 对不确定的参数，用自然语言向用户提问
4. 生成完整的测试用例（包含 name, method, url, headers, params, body, auth, assertions）
5. 保存到数据库

## 约束
- 不要问用户可以自己推断的问题
- 保留 {{var}} 模板语法用于环境变量
- 确保每个请求的必填字段都有值
- 每次只问 1-3 个最关键的问题，不要一次性问太多
"""

ENDPOINT_GENERATION_PROMPT = """你是一个 API 测试用例生成专家。请为以下 API 端点生成测试用例：

端点: {method} {path}
名称: {summary}
描述: {description}
参数: {parameters}

## 生成要求
每条生成记录必须包含以下字段：
- name: 请求名称（必填，最长 200 字符）
- method: GET/POST/PUT/DELETE/PATCH 之一
- url: 请求路径（必填，路径参数使用 {{{{param}}}} 语法）
- headers: 请求头键值对
- params: URL 查询参数键值对
- body: 请求体对象
- auth: 认证信息，必须有 type 字段
- assertions: 断言列表
- pre_request_script: 请求前脚本
- post_request_script: 请求后脚本

## 输出格式约束
{output_schema}
"""

OUTPUT_SCHEMA_CONSTRAINT = """必须严格按以下 JSON 结构输出，字段类型与示例一致：

{{
  "name": "获取用户列表",
  "method": "GET",
  "url": "/api/users",
  "headers": {{"Authorization": "Bearer {{{{API_TOKEN}}}}"}},
  "params": {{"page": "1", "page_size": "10"}},
  "body": {{}},
  "auth": {{"type": "none"}},
  "assertions": [
    {{"type": "status_code", "expected": 200}}
  ],
  "pre_request_script": "",
  "post_request_script": ""
}}

约束规则：
- name 必填，最长 200 字符
- method 必须是 GET/POST/PUT/DELETE/PATCH 之一
- url 必填，路径参数使用 {{{{param}}}} 语法
- headers/params 必须是键值对对象（不能是数组）
- auth 必须有 type 字段
"""
```

- [ ] **Step 6: 提交**

```bash
cd d:/AI/testhub
git add apps/api_testing/ai_agent/__init__.py apps/api_testing/ai_agent/schema.py apps/api_testing/ai_agent/prompts.py apps/api_testing/tests/test_ai_agent_schema.py
git commit -m "feat: add AI agent schema validation and prompt templates"
```

---

### Task 2: 持久化模块 + 工具集

**Files:**
- Create: `apps/api_testing/ai_agent/persistence.py`
- Create: `apps/api_testing/ai_agent/tools.py`
- Test: `apps/api_testing/tests/test_ai_agent_tools.py`

**Interfaces:**
- Consumes: `validate_llm_output()` from schema.py
- Consumes: `doc_parser.parse_document()`, `ai_import_service.analyze_endpoints()`
- Produces: `DjangoCheckpointSaver` — LangGraph checkpoint saver
- Produces: 4 `@tool` functions: `parse_document_tool`, `classify_parameters_tool`, `generate_test_cases_tool`, `save_to_database_tool`

- [ ] **Step 1: 编写 persistence.py**

```python
# apps/api_testing/ai_agent/persistence.py
"""LangGraph checkpoint 持久化到 AIImportTask.generated_summary 字段"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from langgraph.checkpoint import BaseCheckpointSaver

logger = logging.getLogger(__name__)


class DjangoCheckpointSaver(BaseCheckpointSaver):
    """将 Agent 的检查点保存到 AIImportTask.generated_summary 字段中。"""

    def put(self, config: Dict[str, Any], checkpoint: Dict[str, Any],
            metadata: Dict[str, Any]) -> Dict[str, Any]:
        from apps.api_testing.models import AIImportTask

        task_id = config.get("configurable", {}).get("task_id")
        if not task_id:
            return config

        try:
            task = AIImportTask.objects.get(id=task_id)
            task.generated_summary = {
                "agent_state": checkpoint,
                "agent_metadata": metadata,
            }
            task.save(update_fields=["generated_summary"])
        except AIImportTask.DoesNotExist:
            logger.warning("DjangoCheckpointSaver: task %s not found", task_id)

        return config

    def get(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from apps.api_testing.models import AIImportTask

        task_id = config.get("configurable", {}).get("task_id")
        if not task_id:
            return None

        try:
            task = AIImportTask.objects.get(id=task_id)
            summary = task.generated_summary
            if isinstance(summary, dict) and "agent_state" in summary:
                return summary["agent_state"]
        except AIImportTask.DoesNotExist:
            pass

        return None
```

- [ ] **Step 2: 编写 tools.py**

```python
# apps/api_testing/ai_agent/tools.py
"""Agent 工具集 — 4 个 @tool 函数"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from apps.api_testing.ai_agent.schema import validate_llm_output

logger = logging.getLogger(__name__)


@tool
def parse_document_tool(raw_content: dict) -> List[dict]:
    """解析上传的 API 文档内容，提取端点列表。
    支持格式: Swagger 2.0, OpenAPI 3.0, Postman Collection, HAR。
    返回归一化的端点列表 [{path, method, summary, parameters, ...}]"""
    from apps.api_testing.doc_parser import parse_document

    endpoints = parse_document(raw_content)
    logger.info("parse_document_tool: parsed %d endpoints", len(endpoints))
    return endpoints


@tool
def classify_parameters_tool(endpoints: List[dict]) -> dict:
    """分析端点的所有参数，按 auto/manual/context_ref 分类。
    - auto: LLM 可以自动生成测试值
    - manual: 需要用户提供值
    - context_ref: 引用已有上下文"""
    from apps.api_testing.ai_import_service import analyze_endpoints

    classification = analyze_endpoints(endpoints)
    logger.info(
        "classify_parameters_tool: %d endpoints, %d auto, %d manual, %d context_ref",
        classification.get("endpoint_count", 0),
        classification.get("auto_params", 0),
        classification.get("manual_params", 0),
        classification.get("context_ref_params", 0),
    )
    return classification


@tool
def generate_test_cases_tool(
    endpoints: List[dict],
    classification: dict,
    user_answers: dict,
    tester_prompt: str,
) -> List[dict]:
    """为每个端点生成完整的 API 测试用例。
    使用 tester.md 提示词规范，输出必须符合 ApiRequest 模型字段 schema。"""
    from apps.api_testing.ai_import_service import generate_requests

    environment_vars = user_answers.get("environment_vars", {})
    answers = user_answers.get("answers", {})

    raw_requests = generate_requests(
        endpoints, classification, answers, environment_vars,
    )

    try:
        validated = validate_llm_output(raw_requests)
        logger.info("generate_test_cases_tool: %d requests validated", len(validated))
        return validated
    except ValueError as e:
        logger.warning("generate_test_cases_tool: validation failed: %s", e)
        raise


@tool
def save_to_database_tool(
    requests: List[dict],
    project_id: int,
    collection_id: Optional[int],
    auto_structure: bool,
    user_id: int,
) -> dict:
    """将生成的 API 请求保存到数据库。
    按标签自动创建 ApiCollection，或者保存到指定集合。
    返回 {collections_created: [], requests_created: []}"""
    from django.db import transaction
    from apps.api_testing.models import ApiProject, ApiCollection, ApiRequest

    with transaction.atomic():
        project = ApiProject.objects.get(id=project_id)

        created_collections: List[int] = []
        created_request_ids: List[int] = []

        if auto_structure:
            # 按 name 前缀或 description 分组
            tag_requests: Dict[str, list] = {"Default": []}
            for req in requests:
                tag_requests["Default"].append(req)

            # 创建一个基于时间的集合名
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            collection, _ = ApiCollection.objects.get_or_create(
                project=project,
                name=f"AI Agent Import {now_str}",
                defaults={"description": "AI Agent 自动导入的 API 集合"},
            )
            created_collections.append(collection.id)

            for req_data in requests:
                api_request = ApiRequest.objects.create(
                    collection=collection,
                    name=str(req_data.get("name", ""))[:200],
                    description=req_data.get("description", ""),
                    method=req_data.get("method", "GET"),
                    url=req_data.get("url", ""),
                    headers=req_data.get("headers", {}),
                    params=req_data.get("params", {}),
                    body=req_data.get("body", {}),
                    auth=req_data.get("auth", {}),
                    assertions=req_data.get("assertions", []),
                    pre_request_script=req_data.get("pre_request_script", ""),
                    post_request_script=req_data.get("post_request_script", ""),
                    created_by_id=user_id,
                )
                created_request_ids.append(api_request.id)
        else:
            if collection_id:
                collection = ApiCollection.objects.get(id=collection_id)
            else:
                from datetime import datetime
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                collection = ApiCollection.objects.create(
                    project=project,
                    name=f"AI Agent Import {now_str}",
                    description="AI Agent 自动导入的 API 集合",
                )
                created_collections.append(collection.id)

            for req_data in requests:
                api_request = ApiRequest.objects.create(
                    collection=collection,
                    name=str(req_data.get("name", ""))[:200],
                    description=req_data.get("description", ""),
                    method=req_data.get("method", "GET"),
                    url=req_data.get("url", ""),
                    headers=req_data.get("headers", {}),
                    params=req_data.get("params", {}),
                    body=req_data.get("body", {}),
                    auth=req_data.get("auth", {}),
                    assertions=req_data.get("assertions", []),
                    pre_request_script=req_data.get("pre_request_script", ""),
                    post_request_script=req_data.get("post_request_script", ""),
                    created_by_id=user_id,
                )
                created_request_ids.append(api_request.id)

    logger.info(
        "save_to_database_tool: %d collections, %d requests created",
        len(created_collections), len(created_request_ids),
    )
    return {
        "collections_created": created_collections,
        "requests_created": created_request_ids,
    }
```

- [ ] **Step 3: 编写 tools 测试**

```python
# apps/api_testing/tests/test_ai_agent_tools.py
import pytest
from apps.api_testing.ai_agent.tools import (
    parse_document_tool,
    classify_parameters_tool,
)


class TestParseDocumentTool:
    def test_swagger_2(self):
        """解析 Swagger 2.0 文档"""
        raw = {
            "swagger": "2.0",
            "info": {"title": "Pet Store", "version": "1.0"},
            "paths": {
                "/pets": {
                    "get": {
                        "summary": "List pets",
                        "parameters": [
                            {"name": "limit", "in": "query", "type": "integer"},
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        result = parse_document_tool.invoke({"raw_content": raw})
        assert len(result) >= 1
        # 第一个端点应该是 GET /pets
        pet_endpoint = None
        for ep in result:
            if ep.get("path") == "/pets" and ep.get("method") == "get":
                pet_endpoint = ep
                break
        assert pet_endpoint is not None, f"Could not find GET /pets in {result}"


class TestClassifyParametersTool:
    def test_classify_simple_endpoints(self):
        endpoints = [
            {
                "path": "/api/users",
                "method": "GET",
                "summary": "Get users",
                "parameters": [
                    {"name": "page", "in": "query", "type": "integer"},
                    {"name": "search", "in": "query", "type": "string"},
                ],
            }
        ]
        result = classify_parameters_tool.invoke({"endpoints": endpoints})
        assert result["endpoint_count"] == 1
        assert result["total_params"] == 2
```

- [ ] **Step 4: 运行 tools 测试**

Run: `cd d:/AI/testhub && python -m pytest apps/api_testing/tests/test_ai_agent_tools.py -v`
Expected: Tests PASS (可能 swagger 解析在 doc_parser 中的具体行为需要调整断言)

- [ ] **Step 5: 提交**

```bash
cd d:/AI/testhub
git add apps/api_testing/ai_agent/persistence.py apps/api_testing/ai_agent/tools.py apps/api_testing/tests/test_ai_agent_tools.py
git commit -m "feat: add agent persistence and tool functions"
```

---

### Task 3: Agent 核心引擎

**Files:**
- Create: `apps/api_testing/ai_agent/agent.py`
- Test: `apps/api_testing/tests/test_ai_agent_agent.py`

**Interfaces:**
- Consumes: `DjangoCheckpointSaver`, `parse_document_tool`, `classify_parameters_tool`, `generate_test_cases_tool`, `save_to_database_tool`
- Consumes: `AGENT_SYSTEM_PROMPT`, `ENDPOINT_GENERATION_PROMPT`
- Produces: `ImportAgent` class with `__init__(task)`, `run()`, `resume(user_message, user_answers)` methods
- Produces: `AgentState` TypedDict

- [ ] **Step 1: 编写 agent.py**

```python
# apps/api_testing/ai_agent/agent.py
"""LangGraph StateGraph 驱动的主 Agent"""
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
from apps.api_testing.ai_agent.schema import validate_llm_output

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


def _call_llm(messages: List[dict]) -> str:
    """调用 LLM 获取回复"""
    from apps.requirement_analysis.models import AIModelConfig, AIModelService

    config = AIModelConfig.objects.filter(is_active=True).first()
    if not config:
        raise RuntimeError("No active AIModelConfig found")

    response = asyncio.run(
        AIModelService.call_openai_compatible_api(config, messages)
    )
    return response["choices"][0]["message"]["content"]


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
            "tester_prompt": ENDPOINT_GENERATION_PROMPT.format(
                method="{method}",
                path="{path}",
                summary="{summary}",
                description="{description}",
                parameters="{parameters}",
                output_schema=OUTPUT_SCHEMA_CONSTRAINT,
            ),
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
        self.graph = build_agent_graph()
        self.checkpointer = DjangoCheckpointSaver()

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
        result = self.graph.invoke(initial_state, config, checkpoint=self.checkpointer)
        return self._update_task(result)

    def resume(self, user_message: str, user_answers: dict) -> AgentState:
        """用户回复后恢复 Agent 执行"""
        from apps.api_testing.models import AIImportTask

        # 从 checkpoint 恢复
        config = {"configurable": {"task_id": self.task_id}}
        saved_state = self.checkpointer.get(config) or {}

        saved_state["user_answers"] = user_answers
        saved_state["messages"] = saved_state.get("messages", []) + [
            {"role": "user", "content": user_message}
        ]

        result = self.graph.invoke(
            saved_state, config, checkpoint=self.checkpointer,
        )
        return self._update_task(result)

    def _update_task(self, state: AgentState) -> AgentState:
        """将 Agent 状态同步到 AIImportTask"""
        from apps.api_testing.models import AIImportTask

        try:
            task = AIImportTask.objects.get(id=self.task_id)
            task.status = state.get("status", "failed")
            task.progress = state.get("progress", 0)
            if state.get("parsed_endpoints"):
                task.parsed_endpoints = state["parsed_endpoints"]
            if state.get("classification"):
                task.ai_classification = state["classification"]
            if state.get("generated_requests"):
                task.generated_summary = {"requests": state["generated_requests"]}
            if state.get("error"):
                task.error_message = state["error"]
            task.save()
        except AIImportTask.DoesNotExist:
            logger.warning("ImportAgent: task %s not found for state sync", self.task_id)

        return state
```

- [ ] **Step 2: 编写 agent 测试**

```python
# apps/api_testing/tests/test_ai_agent_agent.py
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
        assert graph.entry_point == "parse"


class TestImportAgent(TestCase):
    def test_import_agent_init(self):
        """验证 ImportAgent 初始化"""
        agent = ImportAgent(task_id=999)
        assert agent.task_id == 999
        assert agent.graph is not None
        assert agent.checkpointer is not None
```

- [ ] **Step 3: 运行 agent 测试**

Run: `cd d:/AI/testhub && python -m pytest apps/api_testing/tests/test_ai_agent_agent.py -v`
Expected: Tests PASS

- [ ] **Step 4: 提交**

```bash
cd d:/AI/testhub
git add apps/api_testing/ai_agent/agent.py apps/api_testing/tests/test_ai_agent_agent.py
git commit -m "feat: add LangGraph agent core engine with ImportAgent class"
```

---

### Task 4: 后端 API 端点

**Files:**
- Modify: `apps/api_testing/views.py` (新增 `agent_state` + `agent_reply` actions)
- Modify: `apps/api_testing/serializers.py` (新增 `AgentReplySerializer`)
- Modify: `apps/api_testing/urls.py` (无需改动 — DRF router 自动注册 @action)

**Interfaces:**
- Consumes: `ImportAgent` class from agent.py
- Produces: `GET /api-testing/ai-import/{id}/agent-state/` — 获取 Agent 当前状态
- Produces: `POST /api-testing/ai-import/{id}/agent-reply/` — 用户回复 Agent

- [ ] **Step 1: 添加 AgentReplySerializer**

在 `apps/api_testing/serializers.py` 末尾添加：

```python
class AgentReplySerializer(serializers.Serializer):
    """Agent 回复序列化器"""
    message = serializers.CharField(required=False, allow_blank=True, default="")
    answers = serializers.JSONField(required=False, default=dict)

    def validate_answers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("answers 必须是对象")
        return value
```

- [ ] **Step 2: 在 AIImportViewSet 中添加 agent_state action**

在 `AIImportViewSet` 类末尾（`save` action 之后）、`logs` action 之前，添加：

```python
    # ----- 7. Agent 状态查询 -----
    @action(detail=True, methods=['get'])
    def agent_state(self, request, pk=None):
        """获取 Agent 当前状态（对话历史、进度、待回答问题）"""
        task = self.get_object()

        # 从 generated_summary 中恢复 agent 消息
        messages = []
        agent_data = {}
        if isinstance(task.generated_summary, dict):
            agent_data = task.generated_summary.get("agent_state", {})
            messages = task.generated_summary.get("agent_messages", [])

        return Response({
            "status": task.status,
            "progress": task.progress,
            "messages": messages,
            "parsed_endpoints": task.parsed_endpoints or [],
            "classification_summary": {
                "endpoint_count": (task.ai_classification or {}).get("endpoint_count", 0),
                "total_params": (task.ai_classification or {}).get("total_params", 0),
                "auto_params": (task.ai_classification or {}).get("auto_params", 0),
                "manual_params": (task.ai_classification or {}).get("manual_params", 0),
            } if task.ai_classification else None,
        })

    # ----- 8. Agent 回复 -----
    @action(detail=True, methods=['post'])
    def agent_reply(self, request, pk=None):
        """用户回复 Agent 的提问，触发 Agent 继续执行"""
        task = self.get_object()
        serializer = AgentReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_message = serializer.validated_data.get("message", "")
        user_answers = serializer.validated_data.get("answers", {})

        try:
            agent = ImportAgent(task.id)
            result = agent.resume(user_message, user_answers)

            # 保存对话消息到 task
            messages = result.get("messages", [])
            if isinstance(task.generated_summary, dict):
                task.generated_summary["agent_messages"] = messages
                task.save(update_fields=["generated_summary"])

            return Response({
                "status": result.get("status"),
                "progress": result.get("progress", 0),
                "messages": messages[-10:],
                "error": result.get("error"),
            })
        except Exception as e:
            logger.exception("agent_reply failed")
            return Response(
                {"error": f"Agent 处理失败: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
```

- [ ] **Step 3: 编写 API 测试**

```python
# apps/api_testing/tests/test_ai_agent_api.py
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
```

- [ ] **Step 4: 运行 API 测试**

Run: `cd d:/AI/testhub && python -m pytest apps/api_testing/tests/test_ai_agent_api.py -v`
Expected: Tests PASS

- [ ] **Step 5: 提交**

```bash
cd d:/AI/testhub
git add apps/api_testing/views.py apps/api_testing/serializers.py apps/api_testing/tests/test_ai_agent_api.py
git commit -m "feat: add agent-state and agent-reply API endpoints"
```

---

### Task 5: 前端 Agent 对话组件

**Files:**
- Modify: `frontend/src/api/api-testing-import.js`
- Modify: `frontend/src/views/api-testing/AIImportWizard.vue`

- [ ] **Step 1: 更新前端 API 层**

```javascript
// 在 frontend/src/api/api-testing-import.js 末尾添加

export function getAgentState(taskId) {
  return request({
    url: `/api-testing/ai-import/${taskId}/agent-state/`,
    method: 'get'
  })
}

export function sendAgentReply(taskId, data) {
  return request({
    url: `/api-testing/ai-import/${taskId}/agent-reply/`,
    method: 'post',
    data
  })
}
```

- [ ] **Step 2: 修改 AIImportWizard.vue — 步骤条**

将 steps 从 6 步改为 4 步（移除 Analysis/Questions/Generate，替换为 Agent Chat）：

第 8-14 行改为：

```html
<el-steps :active="currentStep" align-center class="wizard-steps">
  <el-step :title="$t('apiTesting.aiImport.stepUpload')" />
  <el-step :title="$t('apiTesting.aiImport.stepConfig')" />
  <el-step :title="$t('apiTesting.aiImport.stepAgent')" />
  <el-step :title="$t('apiTesting.aiImport.stepResults')" />
</el-steps>
```

- [ ] **Step 3: 替换 Step 2-4 为 Agent Chat 界面**

将 Step 2 (Analysis)、Step 3 (Questions)、Step 4 (Generate) 的 `<div v-show>` 区块整体替换为：

```html
<!-- Step 2: Agent Chat -->
<div v-show="currentStep === 2" class="step-panel agent-chat-panel">
  <el-card class="chat-card">
    <div class="chat-messages" ref="chatRef">
      <div v-for="(msg, idx) in chatMessages" :key="idx"
           :class="['message', msg.role === 'agent' ? 'agent-message' : 'user-message']">
        <div class="message-bubble">
          <div class="message-avatar">
            <el-avatar :size="32" :icon="msg.role === 'agent' ? 'Monitor' : 'UserFilled'" />
          </div>
          <div class="message-content">
            <div class="message-text">{{ msg.content }}</div>
          </div>
        </div>
      </div>
      <div v-if="agentLoading" class="message agent-message">
        <div class="message-bubble">
          <div class="message-avatar">
            <el-avatar :size="32" icon="Monitor" />
          </div>
          <div class="message-content">
            <el-progress :percentage="100" :stroke-width="4" indeterminate />
            <span class="thinking-text">Agent 思考中...</span>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <el-input
        v-model="userInput"
        type="textarea"
        :rows="2"
        :placeholder="$t('apiTesting.aiImport.chatPlaceholder')"
        :disabled="agentLoading"
        @keyup.enter.ctrl="sendAgentMessage"
      />
      <el-button
        type="primary"
        :loading="agentLoading"
        :disabled="!userInput.trim()"
        @click="sendAgentMessage"
      >
        {{ $t('apiTesting.common.send') }}
      </el-button>
    </div>
  </el-card>
</div>
```

- [ ] **Step 4: 添加 Agent chat 相关 data 和 methods**

在 `<script>` 的 `setup()` 中添加：

```javascript
// Agent Chat state
const chatMessages = ref([])
const userInput = ref('')
const agentLoading = ref(false)

// 进入 Agent 步骤时加载状态
const loadAgentState = async () => {
  if (!currentTaskId.value) return
  try {
    agentLoading.value = true
    const res = await getAgentState(currentTaskId.value)
    if (res.messages && res.messages.length > 0) {
      chatMessages.value = res.messages
    } else {
      // 首次进入，添加欢迎消息
      chatMessages.value = [{
        role: 'agent',
        content: `已解析文档并完成参数分析。${res.classification_summary?.manual_params > 0
          ? `发现 ${res.classification_summary.manual_params} 个参数需要您确认，请回复提供这些参数的值。`
          : '所有参数均可自动生成，将直接为您生成测试用例。'}`
      }]
    }
  } catch (e) {
    ElMessage.error('获取 Agent 状态失败')
  } finally {
    agentLoading.value = false
  }
}

const sendAgentMessage = async () => {
  const message = userInput.value.trim()
  if (!message || !currentTaskId.value) return

  chatMessages.value.push({ role: 'user', content: message })
  userInput.value = ''
  agentLoading.value = true

  try {
    const res = await sendAgentReply(currentTaskId.value, { message })
    if (res.messages) {
      chatMessages.value = res.messages
    }
    if (res.status === 'completed') {
      currentStep.value = 3  // 跳转到结果页
      savedCount.value = chatMessages.value[chatMessages.value.length - 1]?.content || 0
      await loadResults()
    }
  } catch (e) {
    ElMessage.error('Agent 处理失败')
    chatMessages.value.push({
      role: 'agent',
      content: '处理出错，请稍后重试'
    })
  } finally {
    agentLoading.value = false
  }
}

// 在 watch 中监听 step 变化，进入 Step 2 时自动加载
watch(currentStep, (step) => {
  if (step === 2) {
    loadAgentState()
  }
})
```

- [ ] **Step 5: 添加 Chat CSS 样式**

在 `<style scoped>` 中添加：

```css
.agent-chat-panel {
  max-width: 900px;
  margin: 0 auto;
}

.chat-card {
  min-height: 400px;
  display: flex;
  flex-direction: column;
}

.chat-messages {
  flex: 1;
  max-height: 500px;
  overflow-y: auto;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
  margin-bottom: 16px;
}

.message {
  margin-bottom: 16px;
}

.message-bubble {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.user-message .message-bubble {
  flex-direction: row-reverse;
}

.message-text {
  padding: 8px 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  max-width: 70%;
  white-space: pre-wrap;
  line-height: 1.5;
}

.user-message .message-text {
  background: #409eff;
  color: #fff;
  border-color: #409eff;
}

.chat-input-area {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.chat-input-area .el-textarea {
  flex: 1;
}

.thinking-text {
  margin-left: 8px;
  color: #909399;
  font-size: 13px;
}
```

- [ ] **Step 6: 验证前端构建**

Run: `cd d:/AI/testhub/frontend && npm run build --noEmit` (或 `npm run lint`)
Expected: Build succeeds

- [ ] **Step 7: 提交**

```bash
cd d:/AI/testhub
git add frontend/src/api/api-testing-import.js frontend/src/views/api-testing/AIImportWizard.vue
git commit -m "feat: add agent chat UI replacing steps 2-4"
```

---

## 未包含在计划中的内容（后续优化）

- SSE 流式输出 Agent 思考过程（P2 优化项）
- Agent 超时/重试机制（P3 优化项）
- 回退到旧流水线的兜底策略（P3 优化项）
- 对话历史缓存与清理（P3 优化项）
- i18n 中 agent chat 相关文案翻译（现有 en/zh-cn 需补充）
- 内联表单渲染（参数表格、环境变量映射等复杂交互组件）

这些内容不适合当前计划的 TDD 节奏，应在后续优化迭代中按需添加。
