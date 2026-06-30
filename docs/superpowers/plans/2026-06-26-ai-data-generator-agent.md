# AI 接口测试数据生成 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI-powered test data generation agent in DataFactory that classifies API parameters (auto/manual/context_ref), collects human input for manual fields, retrieves project-context, and generates valid test data with post-validation.

**Architecture:** Multi-stage LangGraph agent with human-in-the-loop interrupt. Stage 1 — LLM classifies each field as auto/manual/context_ref. Stage 2 — manual fields shown to user for input (no manual fields → skip). Stage 3 — context retriever queries same-project API test cases for reusable data. Stage 4 — LLM generates final dataset with classification + inputs + context. Post-generation — type-system validators verify format correct, fallback to DataFactory tool fixers on failure.

**Tech Stack:** LangChain + langgraph (new dependency), existing `AIModelService.call_openai_compatible_api_stream()`, existing DataFactory tools as fixers, existing `VariableResolver` for variable injection, SSE for streaming, Vue 3 + Element Plus for frontend.

## Global Constraints

- All AI configs use existing `AIModelConfig` / `PromptConfig` system — no new Model table
- New endpoints under `/api/data-factory/` prefix via `@action` decorators
- LangGraph agent uses `StateGraph` + `interrupt` for human-in-the-loop
- Type validators are regex-based; fixers delegate to existing `DataFactory` deterministic tools
- Prompt files stored in `docs/` with `load_defaults` pattern (same as writer/reviewer/vision)
- All frontend AI UI is a new `<el-tab-pane>` inside `DataFactory.vue` — not a separate route
- i18n: add both zh-cn and en locale keys

---

## File Structure

### Files to modify:
- `apps/requirement_analysis/models.py` — add `data_generator` to `AIModelConfig.ROLE_CHOICES` and `PromptConfig.PROMPT_CHOICES`
- `apps/requirement_analysis/views.py` — add `data_generator` to `load_defaults()`, register prompt file
- `apps/data_factory/serializers.py` — add `AIFieldDefSerializer`, `AIClassifyResultSerializer`, `AIGenerateRequestSerializer`
- `apps/data_factory/views.py` — add `ai_classify`, `ai_generate`, `ai_context` `@action` endpoints
- `frontend/src/views/data-factory/DataFactory.vue` — add new `<el-tab-pane>` switching between tool mode and AI mode
- `frontend/src/api/data-factory.js` — add `classifyFields()`, `generateData()`, `getProjectContext()` methods
- `frontend/src/locales/lang/zh-cn/data-factory.js` — i18n keys for AI tab

### Files to create:
- `docs/tester_data_gen.md` — AI data generation prompt template (modeled on AItest `提示词-AI生成测试数据.txt`)
- `docs/tester_field_classify.md` — AI field classification prompt template
- `apps/data_factory/ai_agent.py` — LangGraph StateGraph agent (classify → interrupt → retrieve → generate)
- `apps/data_factory/ai_context.py` — Project context retriever (query same-project API test cases)
- `apps/data_factory/ai_types.py` — Type system: schemas, validators, and DataFactory fixers
- `frontend/src/views/data-factory/AIDataGenerator.vue` — AI generation wizard component (field config → manual fill → streaming result)

---

## Tasks

### Task 1: Backend — Add `data_generator` role to AIModelConfig and PromptConfig

**Files:**
- Modify: `apps/requirement_analysis/models.py` line 215-220 (ROLE_CHOICES), line 258-262 (PROMPT_CHOICES)
- Modify: `apps/requirement_analysis/views.py` line 1271-1370 (load_defaults)

**Interfaces:**
- Consumes: existing `AIModelConfig` and `PromptConfig` models
- Produces: `AIModelConfig.get_active_config(model_type, 'data_generator')` available, `PromptConfig.get_active_config('data_generator')` available, prompt files loadable via `load_defaults` API

- [ ] **Step 1: Add role choice to AIModelConfig**

```python
# In apps/requirement_analysis/models.py, ~line 215
ROLE_CHOICES = [
    ('writer', '测试用例编写专家'),
    ('reviewer', '测试评审专家'),
    ('browser_use_text', 'Browser Use - 文本模式'),
    ('vision', '视觉模型（LVM）'),
    ('data_generator', '测试数据生成'),
]
```

- [ ] **Step 2: Add prompt type choice to PromptConfig**

```python
# In apps/requirement_analysis/models.py, ~line 258
PROMPT_CHOICES = [
    ('writer', '用例编写提示词'),
    ('reviewer', '用例评审提示词'),
    ('vision', '图片分析提示词'),
    ('data_generator', '测试数据生成提示词'),
]
```

- [ ] **Step 3: Create prompt template files**

Create `docs/tester_data_gen.md`:

```markdown
# 任务目标
你是一个测试数据生成专家，根据以下配置生成高质量的接口测试数据。

# 字段配置
每条记录包含以下字段：
{{fields}}

# 生成要求
- 生成数量：{{count}} 条
- 输出格式：{{data_format}}（json/sql/csv）
- 语言：{{language}}
- 数据必须真实合理，字段之间逻辑一致

# 额外上下文
{{context}}

# 输出格式示例（仅作结构参考，字段名以实际配置为准）
{{result_format}}

# 约束
- JSON 格式用 ```json ``` 包裹
- SQL 格式用 ```sql ``` 包裹，包含 INSERT INTO 语句
- CSV 格式直接输出逗号分隔文本，首行为列名
```

Create `docs/tester_field_classify.md`:

```markdown
# 任务目标
分析 API 接口测试的入参字段，判断每个字段的数据是否能由 AI 生成。

# 接口信息
{{api_info}}

# 分类规则
将每个字段分为以下三类：
1. auto — AI 可以独立生成的字段，如：姓名、邮箱、手机号、地址、随机整数、时间戳
2. manual — 必须用户提供的字段，如：已存在的用户名、系统已有订单ID、token、session
3. context_ref — 可从项目已有测试用例中复用的字段，如 auth_token、已注册的用户

# 判断依据
- 字段名含 id、token、session、existing 等关键词 → manual
- 类型为 email、phone、name、address、date 等个人/随机数据 → auto
- 描述中提到"已注册"、"已创建"、"已存在" → manual
- 描述中提到"登录后获取"、"上一步返回" → context_ref

# 输出格式
```json
{
  "classification": [
    {"field": "字段名", "type": "auto|manual|context_ref", "reason": "判断理由"}
  ],
  "manual_fields": [
    {"field": "字段名", "prompt": "给用户的填写提示"}
  ],
  "context_fields": [
    {"field": "字段名", "prompt": "需要从项目中检索什么样的数据"}
  ]
}
```
```

- [ ] **Step 4: Update load_defaults in PromptConfigViewSet**

In `apps/requirement_analysis/views.py`, inside `load_defaults()`, add after the vision section:

```python
# 读取测试数据生成提示词
data_gen_prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_data_gen.md')
try:
    with open(data_gen_prompt_path, 'r', encoding='utf-8') as f:
        defaults['data_generator'] = f.read()
except FileNotFoundError:
    defaults['data_generator'] = '''# 任务目标\n你是一个测试数据生成专家...'''

# 读取字段分类提示词
classify_prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_field_classify.md')
try:
    with open(classify_prompt_path, 'r', encoding='utf-8') as f:
        defaults['field_classify'] = f.read()
except FileNotFoundError:
    defaults['field_classify'] = '''# 任务目标\n分析 API 接口测试的入参字段...'''
```

- [ ] **Step 5: Run migration and verify**

Run: `python manage.py makemigrations requirement_analysis`
Run: `python manage.py migrate requirement_analysis`
Verify: Smoke-test by calling `GET /api/requirement-analysis/prompts/load_defaults/` and confirm `data_generator` exists in response.

- [ ] **Step 6: Commit**

```bash
git add apps/requirement_analysis/models.py apps/requirement_analysis/views.py docs/tester_data_gen.md docs/tester_field_classify.md
git commit -m "feat: add data_generator role and prompt templates for AI test data generation"
```

---

### Task 2: Backend — Type system with validators and DataFactory fixers

**Files:**
- Create: `apps/data_factory/ai_types.py`

**Interfaces:**
- Produces: `validate_field(type_name, value) -> bool`, `fix_field(type_name, value, field_def) -> corrected_value`, `FIELD_SCHEMAS` dict

- [ ] **Step 1: Install dependency**

Run: `pip install pydantic` (if not already present; or use dataclasses)

- [ ] **Step 2: Create ai_types.py**

```python
"""AI 数据生成类型系统 — 验证生成结果，不合格时用 DataFactory 工具修正"""

import re
from .tools.test_data_tools import TestDataTools
from .tools.random_tools import RandomTools

FIELD_SCHEMAS = {
    'email': {
        'pattern': r'^[^\s@]+@[^\s@]+\.[^\s@]+$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_email(count=1)['result'][0],
    },
    'phone': {
        'pattern': r'^1[3-9]\d{9}$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_phone(count=1)['result'][0],
    },
    'id_card': {
        'pattern': r'^\d{17}[\dXx]$',
        'fixer': lambda **kw: TestDataTools.generate_id_card(count=1)['result'][0],
    },
    'name': {
        'pattern': r'^[一-龥a-zA-Z\s]{2,20}$',
        'fixer': lambda **kw: TestDataTools.generate_chinese_name(
            gender=kw.get('gender', 'random'), count=1
        )['result'][0],
    },
    'address': {
        'pattern': r'.{5,100}',
        'fixer': lambda **kw: TestDataTools.generate_chinese_address(count=1)['result'][0],
    },
    'username': {
        'pattern': r'^[a-zA-Z0-9_]{3,20}$',
        'fixer': lambda **kw: RandomTools.random_string(length=8, char_type='alphanumeric', count=1)['result'][0],
    },
    'integer': {
        'pattern': r'^-?\d+$',
        'fixer': lambda **kw: str(RandomTools.random_int(
            min_val=kw.get('min', 0), max_val=kw.get('max', 9999), count=1
        )['result'][0]),
    },
    'date': {
        'pattern': r'^\d{4}-\d{2}-\d{2}$',
        'fixer': lambda **kw: RandomTools.random_date(
            start_date=kw.get('start', '2024-01-01'),
            end_date=kw.get('end', '2026-12-31'),
            count=1, date_format='%Y-%m-%d'
        )['result'][0],
    },
    'company': {
        'pattern': r'^[一-龥\w]{2,50}$',
        'fixer': lambda **kw: TestDataTools.generate_company_name(count=1)['result'][0],
    },
    'bank_card': {
        'pattern': r'^\d{16,19}$',
        'fixer': lambda **kw: TestDataTools.generate_bank_card(count=1)['result'][0],
    },
    'url': {
        'pattern': r'^https?://[^\s/$.?#].[^\s]*$',
        'fixer': lambda **kw: f"https://example.com/{RandomTools.random_string(length=6, char_type='letters', count=1)['result'][0]}",
    },
}


def _get_schema(type_name: str) -> dict | None:
    """获取字段类型对应的 schema"""
    return FIELD_SCHEMAS.get(type_name)


def validate_field(type_name: str, value) -> bool:
    """验证单个字段值是否符合格式要求"""
    if not isinstance(value, str):
        return True  # 非字符串类型跳过（如数字、布尔）
    schema = _get_schema(type_name)
    if not schema:
        return True  # 无 schema 的字段跳过（视为自由文本）
    return bool(re.match(schema['pattern'], value))


def fix_field(type_name: str, value, field_def: dict = None) -> str:
    """修正无效字段值，回退到 DataFactory 确定性工具"""
    schema = _get_schema(type_name)
    if not schema:
        return value
    try:
        return schema['fixer'](**(field_def or {}))
    except Exception:
        return value


def validate_and_fix_record(record: dict, field_defs: list) -> dict:
    """验证整条记录，对每个无效字段执行修正"""
    fixed = {}
    for field_def in field_defs:
        name = field_def['name']
        type_name = field_def.get('type', 'string')
        raw_value = record.get(name, '')

        if not validate_field(type_name, raw_value):
            fixed[name] = fix_field(type_name, raw_value, field_def)
        else:
            fixed[name] = raw_value
    return fixed
```

- [ ] **Step 3: Write unit test for type system**

```python
# In apps/data_factory/tests.py
from django.test import TestCase
from .ai_types import validate_field, fix_field, validate_and_fix_record

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
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.data_factory.tests.AITypesTest -v 2`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/data_factory/ai_types.py apps/data_factory/tests.py
git commit -m "feat: add type system for AI data generation validation and fixers"
```

---

### Task 3: Backend — Project context retriever

**Files:**
- Create: `apps/data_factory/ai_context.py`

**Interfaces:**
- Produces: `retrieve_project_context(project_id) -> dict` with keys: `existing_usernames`, `available_ids`, `related_entities`, `recent_executions`

- [ ] **Step 1: Create ai_context.py**

```python
"""从当前项目中检索可复用的测试数据上下文"""

import logging
from typing import Dict, List, Any
from django.db.models import Q

logger = logging.getLogger(__name__)


def retrieve_project_context(project_id: int) -> Dict[str, Any]:
    """
    检索项目中已有的测试数据上下文。
    
    Args:
        project_id: 项目ID
    
    Returns:
        包含可复用数据的上下文字典
    """
    context = {
        "existing_usernames": [],
        "available_ids": [],
        "related_entities": [],
        "recent_executions_summary": "",
    }

    if not project_id:
        return context

    try:
        _load_api_test_cases(project_id, context)
        _load_execution_history(project_id, context)
    except Exception as e:
        logger.warning(f"Project context retrieval failed: {e}")

    return context


def _load_api_test_cases(project_id: int, context: Dict[str, Any]) -> None:
    """加载项目中的 API 测试用例，提取可复用的数据实体"""
    try:
        from apps.api_testing.models import ApiTestCase
        
        cases = ApiTestCase.objects.filter(
            project_id=project_id
        ).values('name', 'request_body', 'response_body')[:20]

        entities = []
        for case in cases:
            entities.append({
                'name': case.get('name', ''),
                'request_preview': str(case.get('request_body', ''))[:200],
                'response_preview': str(case.get('response_body', ''))[:200],
            })

        context['related_entities'] = entities
    except Exception as e:
        logger.warning(f"Failed to load API test cases for context: {e}")


def _load_execution_history(project_id: int, context: Dict[str, Any]) -> None:
    """加载最近的执行记录，提取成功的响应数据"""
    try:
        from apps.api_testing.models import RequestHistory
        
        recent = RequestHistory.objects.filter(
            project_id=project_id,
            status_code__gte=200,
            status_code__lt=300
        ).order_by('-created_at')[:10]

        executions = []
        for exec_record in recent:
            executions.append({
                'url': exec_record.url or '',
                'method': exec_record.method or '',
                'status': exec_record.status_code,
                'response': str(exec_record.response_body or '')[:300],
            })

        context['recent_executions'] = executions
        # 简单的实体提取：从成功响应中收集 ID 字段
        ids = set()
        for exec_record in recent:
            resp = exec_record.response_body
            if isinstance(resp, dict):
                for key in ('id', 'user_id', 'order_id', 'product_id'):
                    val = resp.get(key)
                    if val is not None:
                        ids.add(str(val))
        context['available_ids'] = sorted(ids)
    except Exception as e:
        logger.warning(f"Failed to load execution history for context: {e}")
```

- [ ] **Step 2: Write unit test**

In `apps/data_factory/tests.py`:
```python
from .ai_context import retrieve_project_context

class AIContextTest(TestCase):
    def test_retrieve_empty_project(self):
        context = retrieve_project_context(99999)
        self.assertIn('related_entities', context)
        self.assertIn('available_ids', context)
```

- [ ] **Step 3: Run test and commit**

Run: `python manage.py test apps.data_factory.tests.AIContextTest -v 2`
Expected: PASS

```bash
git add apps/data_factory/ai_context.py apps/data_factory/tests.py
git commit -m "feat: add project context retriever for AI data generation"
```

---

### Task 4: Backend — LangGraph Agent (core)

**Files:**
- Create: `apps/data_factory/ai_agent.py`
- Modify: `d:\AI\testhub\backend\requirements.txt` — add `langgraph`

**Interfaces:**
- Produces: `run_data_generation_agent(project_id, field_defs, api_info, user_inputs, count, format, language) -> list[dict]`
- Consumes: Task 1's `AIModelConfig.get_active_config(..., 'data_generator')`, Task 2's `validate_and_fix_record()`, Task 3's `retrieve_project_context()`

- [ ] **Step 1: Add langgraph to requirements**

```txt
# In backend/requirements.txt, add:
langgraph>=0.2.0
```

- [ ] **Step 2: Create ai_agent.py — State definition and agent nodes**

```python
"""AI 数据生成 Agent — LangGraph 状态机"""

import json
import logging
import asyncio
from typing import Dict, List, Any, TypedDict, Literal
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint import MemorySaver
from langgraph.types import Command, interrupt

from apps.requirement_analysis.models import AIModelConfig, PromptConfig, AIModelService
from .ai_context import retrieve_project_context
from .ai_types import validate_and_fix_record

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """Agent 运行时状态"""
    project_id: int
    field_defs: list                  # 用户定义的字段 [{name, type, description, ...}]
    api_info: dict                    # 接口信息 {path, method, request_body}
    user_inputs: dict                 # 用户填写的 manual 字段值 {field: value}
    classification: dict | None       # LLM 分类结果
    project_context: dict | None      # 项目上下文
    generated_data: list | None       # 最终生成的数据
    output_format: str                # json / sql / csv
    count: int                        # 生成数量
    language: str                     # 语言
    error: str | None                 # 错误信息


def _get_active_config():
    """获取当前活跃的 data_generator 模型和提示词配置"""
    model_config = AIModelConfig.objects.filter(
        role='data_generator', is_active=True
    ).first()
    prompt_config = PromptConfig.objects.filter(
        prompt_type='data_generator', is_active=True
    ).first()
    return model_config, prompt_config
```

- [ ] **Step 3: Add classification node**

```python
# Continue in ai_agent.py

# ── 节点 1: 字段分类 ──────────────────────────────────────────

def classify_fields(state: AgentState) -> AgentState:
    """LLM 分析接口字段，返回 auto/manual/context_ref 分类"""
    model_config, prompt_config = _get_active_config()
    if not model_config or not prompt_config:
        state['error'] = '未配置 data_generator 模型或提示词'
        return state

    # 加载分类 prompt
    classify_prompt_path = 'docs/tester_field_classify.md'
    try:
        import os
        from django.conf import settings
        with open(os.path.join(settings.BASE_DIR, classify_prompt_path), 'r', encoding='utf-8') as f:
            classify_prompt = f.read()
    except FileNotFoundError:
        classify_prompt = '''# 任务目标\n分析 API 接口测试的入参字段...'''

    api_info_str = json.dumps(state['api_info'], ensure_ascii=False, indent=2)
    prompt = classify_prompt.replace('{{api_info}}', api_info_str)
    
    messages = [
        {"role": "system", "content": "你是一个接口测试专家，严格按格式输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
        content = response['choices'][0]['message']['content']
        # 提取 JSON
        import re
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
```

- [ ] **Step 4: Add routing logic after classification**

```python
# Continue in ai_agent.py

def route_after_classify(state: AgentState) -> Literal["wait_for_user", "retrieve_context", "generate_data"]:
    """分类后路由：有 manual 字段 → 等用户；无 manual 但 context_ref → 检索；否则直接生成"""
    if state['error']:
        return "generate_data"  # 出错时直接尝试生成
    
    classification = state.get('classification', {})
    manual_fields = classification.get('manual_fields', [])
    context_fields = classification.get('context_fields', [])
    
    if manual_fields:
        return "wait_for_user"
    elif context_fields:
        return "retrieve_context"
    else:
        return "generate_data"
```

- [ ] **Step 5: Add human-in-the-loop node**

```python
# Continue in ai_agent.py

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
    
    # 用户返回的值合并到 state（resume 恢复后继续）
    if isinstance(user_values, dict):
        state['user_inputs'] = user_values
    
    return state


def route_after_wait(state: AgentState) -> Literal["retrieve_context", "generate_data"]:
    """用户填写完后的路由：还有 context_ref → 检索，否则直接生成"""
    classification = state.get('classification', {})
    if classification and classification.get('context_fields'):
        return "retrieve_context"
    return "generate_data"
```

- [ ] **Step 6: Add context retrieval and data generation nodes**

```python
# Continue in ai_agent.py

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
            import re
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
```

- [ ] **Step 7: Build and compile the graph**

```python
# Continue in ai_agent.py

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

    return builder.compile(checkpointer=MemorySaver())


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
    """运行数据生成 Agent（流式场景使用同步包装的 SSE，这里提供同步接口）"""
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
```

- [ ] **Step 8: Write integration test**

```python
# In apps/data_factory/tests.py
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
        state = {
            'error': None,
            'classification': {
                'classification': [{'field': 'token', 'type': 'context_ref', 'reason': ''}],
                'manual_fields': [],
                'context_fields': [{'field': 'token', 'prompt': '需要...'}],
            }
        }
        self.assertEqual(route_after_classify(state), 'retrieve_context')
```

- [ ] **Step 9: Run tests and commit**

Run: `python manage.py test apps.data_factory.tests.AIAgentTest -v 2`
Expected: All 3 tests PASS

```bash
git add apps/data_factory/ai_agent.py backend/requirements.txt
git commit -m "feat: add LangGraph agent for AI test data generation"
```

---

### Task 5: Backend — Serializers and API endpoints

**Files:**
- Modify: `apps/data_factory/serializers.py` — add AI serializers
- Modify: `apps/data_factory/views.py` — add `@action` endpoints
- Modify: `apps/data_factory/urls.py` — (no change needed, DefaultRouter covers @action)

**Interfaces:**
- Consumes: Task 4's `run_agent()` async function
- Produces: `POST /api/data-factory/ai_classify/` returns classification JSON, `POST /api/data-factory/ai_generate/` returns SSE stream with validated data, `GET /api/data-factory/ai_context/?project_id=N` returns context JSON

- [ ] **Step 1: Add serializers in serializers.py**

```python
# Add to apps/data_factory/serializers.py

class AIFieldDefSerializer(serializers.Serializer):
    """AI 数据生成字段定义"""
    name = serializers.CharField(required=True)
    type = serializers.CharField(required=False, default='string')
    description = serializers.CharField(required=False, default='')
    # 类型特定参数（用于 fixer）
    min = serializers.IntegerField(required=False)
    max = serializers.IntegerField(required=False)
    gender = serializers.ChoiceField(choices=['random', 'male', 'female'], required=False)


class AIClassifyRequestSerializer(serializers.Serializer):
    """字段分类请求"""
    project_id = serializers.IntegerField(required=True)
    api_info = serializers.JSONField(required=True)
    field_defs = AIFieldDefSerializer(many=True, required=True)


class AIGenerateRequestSerializer(serializers.Serializer):
    """AI 数据生成请求"""
    project_id = serializers.IntegerField(required=True)
    field_defs = AIFieldDefSerializer(many=True, required=True)
    api_info = serializers.JSONField(required=True, default=dict)
    user_inputs = serializers.JSONField(required=False, default=dict)
    classification = serializers.JSONField(required=False, default=None)
    count = serializers.IntegerField(required=False, default=5)
    output_format = serializers.ChoiceField(choices=['json', 'sql', 'csv'], required=False, default='json')
    language = serializers.CharField(required=False, default='中文')
```

- [ ] **Step 2: Add AI endpoints to views.py**

```python
# Add to apps/data_factory/views.py, inside DataFactoryViewSet

from .serializers import (
    DataFactoryRecordSerializer, ToolExecuteSerializer,
    AIFieldDefSerializer, AIClassifyRequestSerializer, AIGenerateRequestSerializer
)
from .ai_context import retrieve_project_context
from .ai_agent import run_agent

from rest_framework.decorators import action
from django.http import StreamingHttpResponse
import json


@action(detail=False, methods=['post'], url_path='ai_classify')
def ai_classify(self, request):
    """分类接口字段：auto / manual / context_ref"""
    serializer = AIClassifyRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    
    from apps.requirement_analysis.models import AIModelConfig, PromptConfig, AIModelService
    
    model_config = AIModelConfig.objects.filter(role='data_generator', is_active=True).first()
    if not model_config:
        return Response({'error': '未配置测试数据生成的AI模型'}, status=400)
    
    # 加载分类 prompt
    import os
    from django.conf import settings
    prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_field_classify.md')
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            classify_prompt = f.read()
    except FileNotFoundError:
        return Response({'error': '字段分类提示词文件未找到'}, status=500)
    
    api_info_str = json.dumps(serializer.validated_data['api_info'], ensure_ascii=False, indent=2)
    prompt = classify_prompt.replace('{{api_info}}', api_info_str)
    
    messages = [
        {"role": "system", "content": "你是一个接口测试专家，严格按格式输出 JSON。"},
        {"role": "user", "content": prompt},
    ]
    
    try:
        import asyncio
        response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
        content = response['choices'][0]['message']['content']
        import re
        json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            result = json.loads(content)
        return Response(result)
    except Exception as e:
        return Response({'error': f'字段分类失败: {e}'}, status=500)


@action(detail=False, methods=['get'], url_path='ai_context')
def ai_context(self, request):
    """获取项目上下文（可复用的测试数据）"""
    project_id = request.query_params.get('project_id')
    if not project_id:
        return Response({'error': '缺少 project_id'}, status=400)
    context = retrieve_project_context(int(project_id))
    return Response(context)


@action(detail=False, methods=['post'], url_path='ai_generate')
def ai_generate(self, request):
    """AI 生成测试数据（SSE 流式）"""
    serializer = AIGenerateRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    
    data = serializer.validated_data
    
    # 先获取分类结果（如果没有传入）
    classification = data.get('classification')
    if not classification:
        # 调用分类
        from apps.requirement_analysis.models import AIModelConfig, AIModelService
        
        model_config = AIModelConfig.objects.filter(role='data_generator', is_active=True).first()
        if not model_config:
            return Response({'error': '未配置测试数据生成的AI模型'}, status=400)
        
        import asyncio
        import os
        from django.conf import settings
        
        prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_field_classify.md')
        with open(prompt_path, 'r', encoding='utf-8') as f:
            classify_prompt = f.read()
        
        api_info_str = json.dumps(data['api_info'], ensure_ascii=False, indent=2)
        prompt = classify_prompt.replace('{{api_info}}', api_info_str)
        
        messages = [
            {"role": "system", "content": "你是一个接口测试专家，严格按格式输出 JSON。"},
            {"role": "user", "content": prompt},
        ]
        
        try:
            response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
            content = response['choices'][0]['message']['content']
            import re
            json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                classification = json.loads(json_match.group(1))
            else:
                classification = json.loads(content)
        except Exception as e:
            return Response({'error': f'字段分类失败: {e}'}, status=500)
    
    # 构建 SSE 流式响应
    def event_stream():
        yield f"data: {json.dumps({'status': 'classification_done', 'classification': classification}, ensure_ascii=False)}\n\n"
        
        # 如果有 manual 字段但用户未提供
        manual_fields = classification.get('manual_fields', [])
        if manual_fields and not data.get('user_inputs'):
            yield f"data: {json.dumps({'status': 'need_user_input', 'fields': manual_fields}, ensure_ascii=False)}\n\n"
            return
        
        # 检索项目上下文
        context = retrieve_project_context(data['project_id'])
        yield f"data: {json.dumps({'status': 'context_retrieved', 'context': context}, ensure_ascii=False)}\n\n"
        
        # 生成数据
        result = asyncio.run(run_agent(
            project_id=data['project_id'],
            field_defs=data['field_defs'],
            api_info=data['api_info'],
            user_inputs=data.get('user_inputs', {}),
            count=int(data.get('count', 5)),
            output_format=data.get('output_format', 'json'),
            language=data.get('language', '中文'),
        ))
        
        if result.get('error'):
            yield f"data: {json.dumps({'status': 'error', 'message': result['error']}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'completed', 'data': result['generated_data']}, ensure_ascii=False)}\n\n"
    
    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream; charset=utf-8'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
```

**Note:** The `ai_generate` endpoint above is a simplified synchronous SSE version. For production, the LangGraph `run_agent` call should be replaced with `AIModelService.call_openai_compatible_api_stream()` for true streaming. This is covered in Task 6 (streaming enhancement).

- [ ] **Step 3: Verify endpoints defined**

Run: `python manage.py show_urls | grep data-factory/ai_`
Expected: three endpoints listed (`ai_classify`, `ai_context`, `ai_generate`)

- [ ] **Step 4: Commit**

```bash
git add apps/data_factory/serializers.py apps/data_factory/views.py
git commit -m "feat: add AI data generation API endpoints"
```

---

### Task 6: Backend — Streaming data generation with SSE

**Files:**
- Modify: `apps/data_factory/views.py` — replace synchronous `run_agent` with streaming LLM call

**Interfaces:**
- Produces: True SSE streaming where each generated record is sent as a separate `data:` event, with live progress

- [ ] **Step 1: Replace ai_generate with real streaming**

Replace the `ai_generate` action in `apps/data_factory/views.py` with a version that streams each record:

```python
@action(detail=False, methods=['post'], url_path='ai_generate')
def ai_generate(self, request):
    """AI 生成测试数据（SSE 流式，逐条输出）"""
    serializer = AIGenerateRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    
    data = serializer.validated_data
    from apps.requirement_analysis.models import AIModelConfig, PromptConfig, AIModelService
    
    model_config = AIModelConfig.objects.filter(role='data_generator', is_active=True).first()
    prompt_config = PromptConfig.objects.filter(prompt_type='data_generator', is_active=True).first()
    
    if not model_config or not prompt_config:
        return Response({'error': '未配置 data_generator 模型或提示词'}, status=400)
    
    def event_stream():
        # 1. 分类（流式）
        classify_prompt_path = os.path.join(settings.BASE_DIR, 'docs/tester_field_classify.md')
        with open(classify_prompt_path, 'r', encoding='utf-8') as f:
            classify_prompt = f.read()
        api_info_str = json.dumps(data['api_info'], ensure_ascii=False, indent=2)
        prompt = classify_prompt.replace('{{api_info}}', api_info_str)
        
        classification = data.get('classification')
        if not classification:
            # 非流式分类（简单场景）
            import asyncio
            messages = [
                {"role": "system", "content": "你是一个接口测试专家，严格按格式输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            try:
                response = asyncio.run(AIModelService.call_openai_compatible_api(model_config, messages))
                content = response['choices'][0]['message']['content']
                import re
                json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
                classification = json.loads(json_match.group(1)) if json_match else json.loads(content)
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
                return
        
        yield f"data: {json.dumps({'status': 'classification_done', 'classification': classification}, ensure_ascii=False)}\n\n"
        
        # 2. 检查 manual 字段
        manual_fields = classification.get('manual_fields', [])
        if manual_fields and not data.get('user_inputs'):
            yield f"data: {json.dumps({'status': 'need_user_input', 'fields': manual_fields}, ensure_ascii=False)}\n\n"
            return
        
        # 3. 检索上下文
        context = retrieve_project_context(data['project_id'])
        yield f"data: {json.dumps({'status': 'context_retrieved', 'context': context}, ensure_ascii=False)}\n\n"
        
        # 4. 构建生成 prompt
        fields_str = "\n".join([
            f"{i+1}. 字段名：{f['name']}\n   - 类型：{f.get('type', 'string')}\n   - 描述：{f.get('description', '')}"
            for i, f in enumerate(data['field_defs'])
        ])
        
        context_parts = []
        if data.get('user_inputs'):
            context_parts.append(f"用户指定值：\n{json.dumps(data['user_inputs'], ensure_ascii=False, indent=2)}")
        if context.get('existing_usernames'):
            context_parts.append(f"已有用户名：{', '.join(context['existing_usernames'])}")
        if context.get('available_ids'):
            context_parts.append(f"可用ID：{', '.join(context['available_ids'])}")
        
        context_str = "\n\n".join(context_parts) if context_parts else "无额外上下文"
        
        prompt_content = prompt_config.content
        prompt_content = prompt_content.replace('{{fields}}', fields_str)
        prompt_content = prompt_content.replace('{{count}}', str(int(data.get('count', 5))))
        prompt_content = prompt_content.replace('{{data_format}}', data.get('output_format', 'json'))
        prompt_content = prompt_content.replace('{{language}}', data.get('language', '中文'))
        prompt_content = prompt_content.replace('{{context}}', context_str)
        prompt_content = prompt_content.replace('{{result_format}}', f"\n```{data.get('output_format', 'json')}\n\n```\n")
        
        messages = [
            {"role": "system", "content": "你是一个测试数据生成专家，严格按格式输出。"},
            {"role": "user", "content": prompt_content},
        ]
        
        # 5. 流式调用 LLM
        full_content = ""
        
        async def stream_callback(chunk):
            nonlocal full_content
            full_content += chunk
        
        try:
            import asyncio
            generator = AIModelService.call_openai_compatible_api_stream(
                model_config, messages, callback=stream_callback
            )
            
            asyncio.run(_consume_generator(generator))
            
            # 6. 解析 + 后验修正
            import re
            json_match = re.search(r'```json\n(.*?)\n```', full_content, re.DOTALL)
            if json_match:
                raw_data = json.loads(json_match.group(1))
            else:
                raw_data = json.loads(full_content)
            
            if isinstance(raw_data, dict):
                raw_data = [raw_data]
            
            validated = []
            for record in raw_data:
                fixed = validate_and_fix_record(record, data['field_defs'])
                validated.append(fixed)
                yield f"data: {json.dumps({'status': 'record', 'index': len(validated) - 1, 'record': fixed}, ensure_ascii=False)}\n\n"
            
            yield f"data: {json.dumps({'status': 'completed', 'total': len(validated), 'data': validated}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
    
    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream; charset=utf-8'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
```

Add helper at bottom of views.py:
```python
async def _consume_generator(generator):
    """消费异步生成器"""
    async for _ in generator:
        pass
```

- [ ] **Step 2: Smoke test SSE endpoint**

Run: `python manage.py runserver`
Test with curl:
```bash
curl -X POST http://localhost:8000/api/data-factory/ai_generate/ \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "field_defs": [{"name": "email", "type": "email"}, {"name": "name", "type": "name"}], "api_info": {"path": "/api/register", "method": "POST"}}'
```
Expected: SSE stream with classification_done → context_retrieved → record* → completed events

- [ ] **Step 3: Commit**

```bash
git add apps/data_factory/views.py
git commit -m "feat: add SSE streaming for AI data generation"
```

---

### Task 7: Frontend — API layer and locale keys

**Files:**
- Modify: `frontend/src/api/data-factory.js` — add 3 new methods
- Modify: `frontend/src/locales/lang/zh-cn/data-factory.js` — add AI tab i18n keys

- [ ] **Step 1: Add API methods**

In `frontend/src/api/data-factory.js`, add:
```javascript
// AI 字段分类
export function aiClassifyFields(data) {
  return request({
    url: '/data-factory/ai_classify/',
    method: 'post',
    data
  })
}

// AI 获取项目上下文
export function getAIContext(projectId) {
  return request({
    url: '/data-factory/ai_context/',
    method: 'get',
    params: { project_id: projectId }
  })
}

// AI 生成测试数据（SSE）
export function aiGenerateData(data, onMessage, onError) {
  const url = '/api/data-factory/ai_generate/'
  const xhr = new XMLHttpRequest()
  
  xhr.open('POST', url, true)
  xhr.setRequestHeader('Content-Type', 'application/json')
  
  xhr.onprogress = () => {
    const lines = xhr.responseText.split('\n')
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6))
          onMessage(event)
        } catch (e) {
          // skip malformed lines
        }
      }
    }
  }
  
  xhr.onerror = () => onError?.('网络请求失败')
  xhr.send(JSON.stringify(data))
  
  return () => xhr.abort()  // return cancel function
}
```

- [ ] **Step 2: Add locale keys**

In `frontend/src/locales/lang/zh-cn/data-factory.js`, add:
```javascript
// AI 生成相关
aiTab: 'AI 生成',
aiTabTooltip: '使用 AI 智能生成测试数据，支持字段分类和自动修正',
fieldConfig: '字段配置',
addField: '添加字段',
removeField: '删除',
fieldName: '字段名',
fieldType: '类型',
fieldDescription: '描述',
generateCount: '生成数量',
outputFormat: '输出格式',
aiGenerate: '开始生成',
generating: '生成中...',
classificationResult: '字段分类结果',
autoField: '自动生成',
manualField: '需手动填写',
contextField: '从项目复用',
fillManualFields: '请填写以下字段',
projectContext: '项目上下文',
retrievedContext: '已检索到以下可复用数据',
generatedResult: '生成结果',
copyAll: '复制全部',
downloadJSON: '下载 JSON',
validationPassed: '✓ 格式验证通过',
validationFixed: '↻ 已自动修正',
selectAll: '全选',
cancelSelect: '取消选择',
deleteSelected: '删除选中',
confirmDelete: '确认删除选中数据吗？',
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/data-factory.js frontend/src/locales/lang/zh-cn/data-factory.js
git commit -m "feat: add AI data generation API layer and locale keys"
```

---

### Task 8: Frontend — AIDataGenerator component

**Files:**
- Create: `frontend/src/views/data-factory/AIDataGenerator.vue`

- [ ] **Step 1: Create component**

```vue
<template>
  <div class="ai-data-generator">
    <!-- Step 1: Field Configuration -->
    <el-card class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">1</el-tag>
          <span class="step-title">{{ $t('dataFactory.fieldConfig') }}</span>
          <el-button size="small" type="success" @click="addField">
            + {{ $t('dataFactory.addField') }}
          </el-button>
        </div>
      </template>
      
      <div v-for="(field, index) in fieldDefs" :key="index" class="field-row">
        <el-row :gutter="10" align="middle">
          <el-col :span="6">
            <el-input v-model="field.name" :placeholder="$t('dataFactory.fieldName')" size="small" />
          </el-col>
          <el-col :span="5">
            <el-select v-model="field.type" size="small" style="width:100%">
              <el-option label="string" value="string" />
              <el-option label="email" value="email" />
              <el-option label="phone" value="phone" />
              <el-option label="name" value="name" />
              <el-option label="address" value="address" />
              <el-option label="id_card" value="id_card" />
              <el-option label="integer" value="integer" />
              <el-option label="date" value="date" />
              <el-option label="company" value="company" />
              <el-option label="bank_card" value="bank_card" />
              <el-option label="url" value="url" />
            </el-select>
          </el-col>
          <el-col :span="10">
            <el-input v-model="field.description" :placeholder="$t('dataFactory.fieldDescription')" size="small" />
          </el-col>
          <el-col :span="3">
            <el-button size="small" type="danger" :icon="Delete" circle @click="removeField(index)" />
          </el-col>
        </el-row>
      </div>
    </el-card>
    
    <!-- Step 2: Config -->
    <el-card class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">2</el-tag>
          <span class="step-title">{{ $t('dataFactory.generateCount') }}</span>
        </div>
      </template>
      <el-row :gutter="20">
        <el-col :span="6">
          <el-form-item :label="$t('dataFactory.generateCount')">
            <el-input-number v-model="count" :min="1" :max="50" />
          </el-form-item>
        </el-col>
        <el-col :span="6">
          <el-form-item :label="$t('dataFactory.outputFormat')">
            <el-select v-model="outputFormat">
              <el-option label="JSON" value="json" />
              <el-option label="SQL" value="sql" />
              <el-option label="CSV" value="csv" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
    </el-card>
    
    <!-- Step 3: Classification Result -->
    <el-card v-if="classification" class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">3</el-tag>
          <span>{{ $t('dataFactory.classificationResult') }}</span>
        </div>
      </template>
      
      <el-tag v-for="item in classification.classification" :key="item.field"
        :type="item.type === 'auto' ? 'success' : (item.type === 'manual' ? 'warning' : 'info')"
        class="classification-tag">
        {{ item.field }}: {{ item.type === 'auto' ? $t('dataFactory.autoField') : 
          (item.type === 'manual' ? $t('dataFactory.manualField') : $t('dataFactory.contextField')) }}
      </el-tag>
    </el-card>
    
    <!-- Step 4: Manual Fields Input -->
    <el-card v-if="manualFields.length > 0 && !generated" class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="warning">!</el-tag>
          <span>{{ $t('dataFactory.fillManualFields') }}</span>
        </div>
      </template>
      
      <el-form label-width="160px">
        <el-form-item v-for="field in manualFields" :key="field.field" :label="field.field">
          <el-input v-model="userInputs[field.field]"
            :placeholder="field.prompt || $t('dataFactory.fillManualFields')" />
          <span class="manual-hint">{{ field.prompt }}</span>
        </el-form-item>
      </el-form>
    </el-card>
    
    <!-- Project Context -->
    <el-card v-if="projectContext" class="step-card context-card">
      <template #header>
        <span>{{ $t('dataFactory.projectContext') }}</span>
      </template>
      <div v-if="projectContext.available_ids?.length" class="context-section">
        <span class="context-label">{{ $t('dataFactory.retrievedContext') }}:</span>
        <el-tag v-for="id in projectContext.available_ids" :key="id" size="small">{{ id }}</el-tag>
      </div>
      <el-empty v-else :description="'无相关上下文'" :image-size="40" />
    </el-card>
    
    <!-- Generate Button -->
    <div class="generate-actions">
      <el-button type="primary" size="large" :loading="loading" 
        :disabled="fieldDefs.length === 0" @click="generate">
        <el-icon v-if="!loading"><MagicStick /></el-icon>
        {{ loading ? $t('dataFactory.generating') : $t('dataFactory.aiGenerate') }}
      </el-button>
    </div>
    
    <!-- Result -->
    <el-card v-if="generatedData.length > 0" class="result-card">
      <template #header>
        <div class="step-header">
          <span>{{ $t('dataFactory.generatedResult') }} ({{ generatedData.length }})</span>
          <div>
            <el-button size="small" @click="copyAll">{{ $t('dataFactory.copyAll') }}</el-button>
            <el-button size="small" type="primary" @click="downloadJSON">{{ $t('dataFactory.downloadJSON') }}</el-button>
          </div>
        </div>
      </template>
      <el-input type="textarea" :rows="10" :model-value="formatResult" readonly />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, MagicStick } from '@element-plus/icons-vue'
import { aiClassifyFields, aiGenerateData } from '@/api/data-factory'

const fieldDefs = ref([{ name: '', type: 'string', description: '' }])
const count = ref(5)
const outputFormat = ref('json')
const loading = ref(false)
const classification = ref(null)
const manualFields = ref([])
const userInputs = ref({})
const projectContext = ref(null)
const generatedData = ref([])
const generated = ref(false)

const formatResult = computed(() => {
  if (outputFormat.value === 'json') {
    return JSON.stringify(generatedData.value, null, 2)
  }
  return generatedData.value.map(r => r.raw || JSON.stringify(r)).join('\n')
})

function addField() {
  fieldDefs.value.push({ name: '', type: 'string', description: '' })
}

function removeField(index) {
  fieldDefs.value.splice(index, 1)
}

async function generate() {
  const validDefs = fieldDefs.value.filter(f => f.name.trim())
  if (validDefs.length === 0) {
    ElMessage.warning('请至少配置一个字段')
    return
  }
  
  loading.value = true
  generatedData.value = []
  generated.value = false
  
  try {
    // Get project ID from current route or global state
    const projectId = 1  // TODO: get from actual project context
    
    // Step 1: Classify fields
    const classifyRes = await aiClassifyFields({
      project_id: projectId,
      field_defs: validDefs,
      api_info: {}
    })
    
    classification.value = classifyRes
    manualFields.value = classifyRes.manual_fields || []
    
    // If has manual fields, wait for user input
    if (manualFields.value.length > 0 && Object.keys(userInputs.value).length === 0) {
      loading.value = false
      ElMessage.info('请填写需要手动输入的字段')
      return
    }
    
    // Step 2: Generate (SSE)
    const cancel = aiGenerateData({
      project_id: projectId,
      field_defs: validDefs,
      api_info: {},
      user_inputs: userInputs.value,
      classification: classifyRes,
      count: count.value,
      output_format: outputFormat.value,
      language: '中文',
    }, (event) => {
      if (event.status === 'context_retrieved') {
        projectContext.value = event.context
      } else if (event.status === 'record') {
        generatedData.value.push(event.record)
      } else if (event.status === 'completed') {
        generated.value = true
        loading.value = false
        ElMessage.success(`成功生成 ${event.total} 条数据`)
      } else if (event.status === 'error') {
        loading.value = false
        ElMessage.error(event.message || '生成失败')
      }
    }, (error) => {
      loading.value = false
      ElMessage.error(error || '网络请求失败')
    })
  } catch (error) {
    loading.value = false
    ElMessage.error(error.response?.data?.error || 'AI 生成失败')
  }
}

function copyAll() {
  navigator.clipboard.writeText(formatResult.value)
  ElMessage.success('已复制到剪贴板')
}

function downloadJSON() {
  const blob = new Blob([formatResult.value], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `test-data-${Date.now()}.${outputFormat.value}`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.ai-data-generator { padding: 16px; }
.step-card { margin-bottom: 16px; }
.step-header { display: flex; align-items: center; gap: 12px; }
.field-row { margin-bottom: 12px; }
.classification-tag { margin: 4px; }
.generate-actions { text-align: center; margin: 24px 0; }
.manual-hint { color: #909399; font-size: 12px; margin-left: 8px; }
.context-section { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.context-label { font-weight: 500; }
.result-card { margin-top: 16px; }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/views/data-factory/AIDataGenerator.vue
git commit -m "feat: add AI data generator component with field config and streaming result"
```

---

### Task 9: Frontend — Integrate AI Tab into DataFactory page

**Files:**
- Modify: `frontend/src/views/data-factory/DataFactory.vue` — add `el-tabs` at top level switching between tool mode and AI mode

- [ ] **Step 1: Add tab structure to DataFactory.vue**

Find the root `<div class="data-factory-container">` and wrap the existing content in a tab pane, adding AI tab:

```vue
<template>
  <div class="data-factory-container">
    <el-card class="header-card">
      <div class="header-content">
        <h1 class="page-title" @click="goToHome">
          <el-icon class="title-icon"><DataLine /></el-icon>
          {{ $t('dataFactory.title') }}
        </h1>
        <p class="page-subtitle">{{ $t('dataFactory.subtitle') }}</p>
      </div>
    </el-card>

    <el-tabs v-model="activeMode" type="border-card" class="mode-tabs">
      <el-tab-pane :label="$t('dataFactory.toolMode')" name="tool">
        <!-- existing content: header-actions, category-view/scenario-view, dialogs -->
        <!-- ... keep all existing DataFactory content here ... -->
      </el-tab-pane>
      <el-tab-pane :label="$t('dataFactory.aiTab')" name="ai">
        <AIDataGenerator />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
```

Replace the `<script>` section to import the new component:

```vue
<script setup>
import { ref } from 'vue'
import AIDataGenerator from './AIDataGenerator.vue'

const activeMode = ref('tool')  // default to tool mode

// ... keep all existing setup code ...
</script>
```

**Important:** The existing header-actions (view mode toggle, history button) should only show when `activeMode === 'tool'`.

Move the `<el-button-group>` and history button inside a `v-if="activeMode === 'tool'"` wrapper.

- [ ] **Step 2: Manually verify tab switching works**

Run: `npm run dev` (in frontend directory)
Open browser, navigate to DataFactory page. Verify:
- Default shows "工具模式" tab with existing tools
- Click "AI 生成" tab → AIDataGenerator component renders
- Switch back → tools still work

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/data-factory/DataFactory.vue
git commit -m "feat: integrate AI data generation tab into DataFactory page"
```

---

### Task 10: End-to-end verification

- [ ] **Step 1: Verify backend endpoints**

Run: `python manage.py runserver`

Test each endpoint:
```bash
# 1. Classification
curl -s -X POST http://localhost:8000/api/data-factory/ai_classify/ \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "api_info": {"path": "/api/register", "method": "POST"}, "field_defs": [{"name": "email", "type": "email"}, {"name": "username", "type": "string", "description": "已注册的用户名"}]}'

# Expected: email → auto, username → manual

# 2. Context
curl -s "http://localhost:8000/api/data-factory/ai_context/?project_id=1"

# 3. Generate SSE
curl -s -N -X POST http://localhost:8000/api/data-factory/ai_generate/ \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "field_defs": [{"name": "email", "type": "email"}, {"name": "name", "type": "name"}], "api_info": {"path": "/api/register", "method": "POST"}, "count": 3}'
```

- [ ] **Step 2: Verify type system fixers**

```python
python manage.py shell -c "
from apps.data_factory.ai_types import validate_field, fix_field
print(validate_field('email', 'bad'))  # False
print(fix_field('email', 'bad'))        # valid email
"
```

- [ ] **Step 3: Run full test suite**

```bash
python manage.py test apps.data_factory.tests -v 2
```

Expected: All tests PASS (existing tests + new AI types/context/agent tests)

- [ ] **Step 4: Final commit with all remaining files**

```bash
git add -A
git commit -m "feat: complete AI test data generation agent with LangGraph, type validation, and frontend UI"
```
