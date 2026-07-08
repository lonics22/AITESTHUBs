# AI 导入功能 Agent 重构方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 用 LangChain + LangGraph 的 AI Agent 架构替换当前三阶段硬编码 Pipeline，实现接口文档上传后由 Agent 自主理解、分类、提问、生成并保存测试用例

**Architecture:** LangGraph StateGraph 驱动的主 Agent + 4 个子工具（文档解析、参数分类、用例生成、数据库保存），Agent 通过自然语言与用户交互，自动决定何时调用工具、何时问用户问题

**Tech Stack:** LangChain + LangGraph（Python），复用现有 `AIModelService` / `AIModelConfig`，提示词复用 `docs/tester.md` 风格

---

## 现状问题

当前 AI 导入是**硬编码三阶段流水线**：

```
upload → parse_document → analyze_endpoints (启发式+LLM) → generate_questions → 用户填表 → generate_requests → save
```

痛点：
1. **流程僵硬** — 6 步 wizard 不可跳过，即使文档已经完整也必须走完所有步骤
2. **LLM 利用率极低** — 只在参数分类时调用 1 次 LLM，其余全靠正则匹配 + 模板代码
3. **用户交互不自然** — 必须渲染固定表单，Agent 不能主动追问或解释
4. **生成质量差** — `generate_requests` 只是简单拼接参数，没有结合接口语义生成有意义的测试数据
5. **提示词未复用** — `docs/tester.md` 已有的高质量测试用例编写能力完全没有被用到

---

## Agent 方案

```
                    ┌─────────────────────────────────┐
                    │      LangGraph StateGraph        │
                    │  (Agent 控制循环)                │
                    │                                  │
                    │  ┌─────┐  ┌──────┐  ┌───────┐  │
                    │  │ Parse │  │ Classify│  │ Generate│  │
                    │  │ Tool  │  │ Tool   │  │ Tool   │  │
                    │  └─────┘  └──────┘  └───────┘  │
                    │  ┌─────┐  ┌──────┐              │
                    │  │ QA   │  │ Save │              │
                    │  │ Tool │  │ Tool │              │
                    │  └─────┘  └──────┘              │
                    └─────────────────────────────────┘
```

### Agent 流程

```
用户上传文档 → Agent 接收 → 调用 ParseTool 提取端点
              → Agent 理解接口语义 → 调用 ClassifyTool 分类参数
              → Agent 判断需要用户确认什么 → 自然语言提问
              → 用户回答 → Agent 结合 tester.md 提示词生成用例
              → 调用 SaveTool 入库 → 完成
```

相比流水线，Agent 的优势：
- **流程自适应** — 文档完整就少问问题，不完整就多问，不走固定步骤
- **LLM 全程参与** — 从理解到生成，每个阶段都用 LLM 处理，而不是只调用一次
- **自然对话** — Agent 直接问"这个接口看起来是获取用户列表，需要我生成分页参数吗？"
- **提示词驱动** — 使用 `docs/tester.md` 和 `docs/tester_pro.md` 作为用例生成规范

---

## 全局约束

- 复用现有 `AIImportTask` 模型字段和状态机，不新建表
- 复用现有的 `AIModelConfig` / `AIModelService` 接口调用 AI
- 复用 `docs/tester.md` 的测试用例编写规范作为 Agent 生成提示词
- 生成的用例仍然存入现有的 `ApiCollection` / `ApiRequest` 模型
- 前端保留 Step 0（上传）和 Step 5（结果），中间步骤由 Agent 接管
- 兼容现有已创建的 AI Import 任务数据
- **LLM 输出必须严格匹配 `ApiRequest` 模型字段**，每一条生成结果必须包含 `name`、`method`、`url`、`headers`、`params`、`body`、`auth`、`assertions` 等字段，类型与数据库字段一致，确保可无错误地直接调用 `ApiRequest.objects.create()` 落库

---

## 模块设计

### 模块 1：Agent 核心引擎

**文件:** `apps/api_testing/ai_agent/agent.py`

LangGraph StateGraph，核心状态定义：

```python
from typing import TypedDict, List, Optional, Any
from langgraph.graph import StateGraph, END

class AgentState(TypedDict):
    """Agent 运行时状态"""
    task_id: int                    # AIImportTask ID
    status: str                     # parsing / classifying / generating / waiting_user / completed / failed
    parsed_endpoints: List[dict]    # 解析后的端点列表
    classification: dict            # 参数分类结果
    generated_requests: List[dict]  # 生成的请求数据
    user_questions: List[dict]      # 等待用户回答的问题
    user_answers: dict              # 用户已回答的内容
    messages: List[dict]            # 对话历史 [role, content]
    error: Optional[str]            # 错误信息

# 构建图
workflow = StateGraph(AgentState)

# 节点
workflow.add_node("parse_document", parse_document_node)
workflow.add_node("classify_parameters", classify_parameters_node)
workflow.add_node("generate_requests", generate_requests_node)
workflow.add_node("ask_questions", ask_questions_node)     # Agent 问用户
workflow.add_node("process_answers", process_answers_node) # 处理用户回答
workflow.add_node("save_results", save_results_node)
workflow.add_node("handle_error", handle_error_node)

# 条件边
workflow.add_conditional_edges(
    "parse_document",
    router_after_parse,  # 成功→classify, 失败→error
)
workflow.add_conditional_edges(
    "classify_parameters",
    router_after_classify,  # 需要提问→ask_questions, 不需要→generate
)
workflow.add_conditional_edges(
    "ask_questions",
    router_after_ask,  # 等待用户回答→pause, 用户已回答→process
)

workflow.set_entry_point("parse_document")
```

**关键设计：** Agent 每次执行到 `ask_questions` 节点时，如果用户尚未回答，则将状态持久化到 `AIImportTask` 并返回给前端，前端收集用户输入后再次触发 Agent 继续执行。这通过 LangGraph 的 `interrupt` 机制实现。

### 模块 2：Agent Tools（工具集）

**文件:** `apps/api_testing/ai_agent/tools.py`

四个核心工具，每个都是 `@tool` 装饰的函数，LLM 可通过 Agent 自主调用：

```python
from langchain.tools import tool

@tool
def parse_document_tool(raw_content: dict) -> List[dict]:
    """解析上传的 API 文档内容，提取端点列表。
    支持格式: Swagger 2.0, OpenAPI 3.0, Postman Collection, HAR
    返回归一化的端点列表 [{path, method, summary, parameters, ...}]"""
    # 复用现有的 doc_parser.parse_document()
    pass

@tool
def classify_parameters_tool(endpoints: List[dict]) -> dict:
    """分析端点的所有参数，按 auto/manual/context_ref 分类。
    - auto: LLM 可以自动生成测试值（分页、时间戳等）
    - manual: 需要用户提供值（token、业务参数等）
    - context_ref: 引用已有上下文（ID、名称等）"""
    # 复用现有的 ai_import_service 分类逻辑
    pass

@tool
def generate_test_cases_tool(
    endpoints: List[dict],
    classification: dict,
    user_answers: dict,
    tester_prompt: str,
) -> List[dict]:
    """使用 tester.md 提示词规范，为每个端点生成完整的 API 测试用例。
    包含: 请求参数组合、headers、body、断言、前置/后置脚本
    输出必须严格符合 ApiRequest 模型字段 schema，参见 _OUTPUT_SCHEMA。"""
    # 调用 LLM+ tester.md 提示词生成用例
    pass

@tool
def save_to_database_tool(
    requests: List[dict],
    project_id: int,
    collection_id: Optional[int],
    auto_structure: bool,
) -> dict:
    """将生成的 API 请求保存到数据库。
    按标签自动创建 ApiCollection，或者保存到指定集合。
    返回 {collections_created: [], requests_created: [], requests_details: []}"""
    pass
```

Agent 通过意图理解决定调用哪个工具以及按什么顺序调用。

### 模块 3：LLM 提示词系统

**文件:** `apps/api_testing/ai_agent/prompts.py`

```python
# Agent 系统提示词 — 定义 Agent 的角色和行为
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
4. 结合 tester.md 规范生成测试用例
5. 保存到数据库

## 输出规范
生成测试用例时，严格遵循以下规范：
{tester_prompt}

## 约束
- 不要问用户可以自己推断的问题
- 保留 {{var}} 模板语法用于环境变量
- 确保每个请求的 url、method、headers 等必填字段都有值
"""

# 用例生成提示词 — 在每个端点生成时使用
ENDPOINT_GENERATION_PROMPT = """你是一个 API 测试用例生成专家。请为以下 API 端点生成测试用例：

端点: {method} {path}
名称: {summary}
描述: {description}
参数: {parameters}

## 生成要求
{tester_prompt}

请按照顺序输出 3-5 个测试用例，覆盖正向流程、异常流程和边界值。
```

### 模块 4：后端 API 变更

**文件:** `apps/api_testing/views.py`

现有端点调整：

| 端点 | 变更 |
|------|------|
| `POST /upload/` | 上传后直接触发 Agent，不再同步执行完整管线 |
| `POST /{id}/answers/` | 改为接收 Agent 的对话响应（自然语言文本或结构化回答），触 发 Agent 继续执行 |
| `GET /{id}/agent-state/` | 新增 — 获取 Agent 当前状态（等待用户回答的问题、对话历史等） |
| `POST /{id}/agent-reply/` | 新增 — 用户回复 Agent 的问询，触 发 Agent 继续 |

取消 `configure`、`questions`、`preview` 端点，这些由 Agent 自主决定何时调用。

```python
# views.py

class AIImportViewSet(viewsets.GenericViewSet):
    # ... 现有代码 ...

    @action(detail=True, methods=['post'])
    def agent_reply(self, request, pk=None):
        """用户回复 Agent 的提问，触 发 Agent 继续执行"""
        task = self.get_object()
        user_message = request.data.get('message', '')
        user_answers = request.data.get('answers', {})

        # 恢复 Agent 状态并注入用户回复
        agent = ImportAgent(task)
        result = agent.resume(user_message, user_answers)

        return Response({
            'state': result.state,
            'questions': result.pending_questions,
            'messages': result.messages[-10:],  # 最近对话
            'progress': result.progress,
        })
```

### 模块 5：前端 Agent 对话组件

**文件:** `frontend/src/views/api-testing/AIImportWizard.vue`

将 Step 2-4 合并为一个**对话式交互界面**：

```
Step 0: 上传文档（保持现有）
Step 1: 项目配置（保持现有，简化）
Step 2: Agent 对话（新增 — 替代 Step 2/3/4）
Step 5: 结果页（保持现有）
```

Step 2 对话组件结构：

```html
<div class="agent-chat">
  <!-- 对话消息列表 -->
  <div class="chat-messages" ref="chatRef">
    <div v-for="msg in chatMessages" :key="msg.id"
         :class="['message', msg.role]">
      <!-- Agent 消息：文字 + 可选表单组件 -->
      <div v-if="msg.role === 'agent'" class="agent-msg">
        <div class="msg-text">{{ msg.content }}</div>
        <!-- Agent 可以渲染内联表单组件 -->
        <div v-if="msg.component === 'env_var_mapping'" class="inline-form">
          <env-var-editor v-model="msg.answers" />
        </div>
      </div>
      <!-- 用户消息：纯文字 -->
      <div v-else class="user-msg">{{ msg.content }}</div>
    </div>
  </div>

  <!-- 输入区域 -->
  <div class="chat-input">
    <el-input
      v-model="userInput"
      type="textarea"
      :rows="2"
      placeholder="回复 Agent 的消息..."
      @keyup.enter.ctrl="sendMessage"
    />
    <el-button type="primary" @click="sendMessage">发送</el-button>
  </div>
</div>
```

Agent 的消息可以是：
1. **纯文本** — "我已解析到 10 个端点，检测到需要认证"
2. **带内联表单** — "请为以下参数提供值" + 内嵌参数表格
3. **确认按钮** — "是否按标签自动创建集合？" + 确认/取消按钮

### 模块 6：LLM 输出 Schema 强制校验

**文件:** `apps/api_testing/ai_agent/schema.py`

LLM 输出的每一条请求必须严格匹配 `ApiRequest` 模型字段，否则数据库写入会失败。定义校验层确保 LLM 输出可落库：

```python
from pydantic import BaseModel, Field, validator
from typing import List, Dict, Any, Optional

# LLM 输出的单条请求 Schema，与 ApiRequest 模型字段一一对应
class ApiRequestSchema(BaseModel):
    """LLM 生成的单条 API 请求，字段与 django model 保持一致"""
    name: str = Field(..., max_length=200, description="请求名称")
    description: str = Field("", description="请求描述")
    method: str = Field("GET", description="请求方法 GET/POST/PUT/DELETE/PATCH")
    url: str = Field(..., description="请求 URL（路径模板使用 {{var}} 语法）")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头键值对")
    params: Dict[str, str] = Field(default_factory=dict, description="URL 查询参数键值对")
    body: Dict[str, Any] = Field(default_factory=dict, description="请求体")
    auth: Dict[str, Any] = Field(default_factory=lambda: {"type": "none"}, description="认证信息")
    assertions: List[Dict[str, Any]] = Field(default_factory=list, description="断言列表")
    pre_request_script: str = Field("", description="请求前脚本")
    post_request_script: str = Field("", description="请求后脚本")

class BatchApiRequestsSchema(BaseModel):
    """LLM 输出的完整批次"""
    requests: List[ApiRequestSchema] = Field(..., description="生成的请求列表")
    total: int = Field(..., description="请求总数")

# 强制校验函数 — 在 generate_test_cases_tool 返回后立即执行
def validate_llm_output(raw: List[dict]) -> List[dict]:
    """校验 LLM 输出的每一条记录，确保字段类型和必填项都符合 ApiRequest 模型。
    
    Raises ValueError 如果校验失败，包含具体字段错误。"""
    batch = BatchApiRequestsSchema(requests=raw, total=len(raw))
    # 额外业务校验
    for i, req in enumerate(batch.requests):
        if req.method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            raise ValueError(f"请求 #{i}: method '{req.method}' 不是合法的 HTTP 方法")
        if not req.url:
            raise ValueError(f"请求 #{i}: url 不能为空")
        if not req.name:
            raise ValueError(f"请求 #{i}: name 不能为空")
    return [req.dict(exclude_none=True) for req in batch.requests]
```

**LLM 输出 → 校验 → 落库** 流程：

```
LLM 原始输出 (JSON string)
  │
  ├─ json.loads() → Python dict
  │
  ├─ validate_llm_output()  ← 这里拦截格式错误
  │    ├─ name: 必填, max_length=200
  │    ├─ method: 必须是 HTTP_METHODS 之一
  │    ├─ url: 必填
  │    ├─ headers: 必须是 Dict[str, str]
  │    ├─ params: 必须是 Dict[str, str]
  │    ├─ body: 必须是 Dict
  │    ├─ auth: 必须有 type 字段
  │    └─ assertions: 必须是 List
  │
  ├─ ✅ 通过 → save_to_database_tool()
  │
  └─ ❌ 失败 → 返回具体字段错误给 LLM 重新生成
      Agent 重试逻辑：
      "生成的请求 #2 url 为空，请补充完整 URL 路径后重新生成"
```

**LLM 提示词中嵌入 Schema 约束** — 在每个 `ENDPOINT_GENERATION_PROMPT` 末尾附上：

```
## 输出 JSON 格式约束
生成的每个请求必须严格按以下 JSON 结构，字段类型与示例一致：

{
  "name": "获取用户列表",
  "description": "分页获取用户列表",
  "method": "GET",
  "url": "/api/users",
  "headers": {"Authorization": "Bearer {{API_TOKEN}}"},
  "params": {"page": "1", "page_size": "10"},
  "body": {},
  "auth": {"type": "none"},
  "assertions": [
    {"type": "status_code", "expected": 200},
    {"type": "json_path", "json_path": "$.code", "expected": 0}
  ],
  "pre_request_script": "",
  "post_request_script": ""
}

约束规则：
- name 必填，最长 200 字符
- method 必须是 GET/POST/PUT/DELETE/PATCH 之一
- url 必填，路径参数使用 {{param}} 语法
- headers/params 必须是键值对对象（不能是数组）
- auth 必须有 type 字段
- assertions 中 type 可选值: status_code/response_time/contains/json_path/header/equals
```

**文件:** `apps/api_testing/ai_agent/persistence.py`

利用 LangGraph 的 `MemorySaver` + Django 模型持久化：

```python
from langgraph.checkpoint import BaseCheckpointSaver
from django.db import transaction

class DjangoCheckpointSaver(BaseCheckpointSaver):
    """将 Agent 的检查点保存到 AIImportTask.generated_summary 字段中"""

    def put(self, config, checkpoint, metadata):
        task = AIImportTask.objects.get(id=config['configurable']['task_id'])
        task.generated_summary = {
            'agent_state': checkpoint,
            'agent_metadata': metadata,
        }
        task.save(update_fields=['generated_summary'])
        return config

    def get(self, config):
        task = AIImportTask.objects.get(id=config['configurable']['task_id'])
        checkpoint = task.generated_summary.get('agent_state') if task.generated_summary else None
        return checkpoint
```

这样 Agent 可以跨请求持久化执行状态，用户回复后恢复执行。

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `apps/api_testing/ai_agent/__init__.py` | 新建 — 包初始化 |
| `apps/api_testing/ai_agent/agent.py` | 新建 — LangGraph StateGraph 定义 |
| `apps/api_testing/ai_agent/tools.py` | 新建 — 4 个 @tool 工具函数 |
| `apps/api_testing/ai_agent/prompts.py` | 新建 — Agent 提示词系统 |
| `apps/api_testing/ai_agent/persistence.py` | 新建 — Django Checkpoint Saver |
| `apps/api_testing/views.py` | 修改 — 新增 agent-reply/agent-state 端点，取消旧端点 |
| `apps/api_testing/serializers.py` | 修改 — 新增 Agent 回复序列化器 |
| `apps/api_testing/urls.py` | 修改 — 注册新端点 |
| `frontend/src/views/api-testing/AIImportWizard.vue` | 修改 — Step 2 改为对话式界面 |
| `frontend/src/api/api-testing-import.js` | 修改 — 替换旧 API 调用为 agent-reply |
| `frontend/src/locales/lang/zh-cn/api-testing.js` | 修改 — 新增对话相关 i18n |
| `frontend/src/locales/lang/en/api-testing.js` | 修改 — 同上 |
| `requirements.txt` | + `langchain`, `langgraph`, `langchain-community` |

---

## 实现优先级

### P0 — Agent 核心引擎 + 工具集
- LangGraph StateGraph 定义和工作流编排
- 4 个工具的 `@tool` 包装
- 状态持久化机制（DjangoCheckpointSaver）
- 后端 agent-reply / agent-state 端点

### P1 — 提示词系统 + LLM 集成
- Agent 系统提示词编写
- 测试用例生成提示词复用 tester.md
- 对接现有的 AIModelService

### P2 — 前端对话组件
- Agent 对话式交互界面
- 内联表单渲染（参数表格、环境变量映射等）
- SSE 流式输出 Agent 思考过程

### P3 — 优化与兜底
- Agent 超时/重试机制
- 回退到旧流水线模式的兜底策略
- 对话历史缓存与清理

---

## 不变的设计原则

1. **Agent 辅助而非替代** — Agent 生成的用例用户可以预览和修改
2. **渐进式替换** — 先增加 Agent 模式作为选项，旧 Pipeline 保留作为兜底
3. **提示词即代码** — 用例生成质量由 `docs/tester.md` 等提示词文件控制，修改提示词即可调整生成策略
4. **兼容现有数据** — Agent 生成的用例仍然存入 `ApiCollection` / `ApiRequest`，不做模型迁移
5. **Schema 强制校验** — LLM 输出必须经过 `validate_llm_output()` 校验层，类型/必填/枚举值不符合时拒绝落库并触发 Agent 重试
