# AI Import 重构方案 — 需求澄清 + 架构设计

## 🎯 需求总结（已确认）

| 需求 | 决策 |
|------|------|
| 交互形式 | **卡片式** — 每个测试用例一张卡片，可编辑字段是输入框 |
| 保存方式 | **一次性确认保存** — 用户填完所有字段后点按钮 |
| 不确定字段 | **表格内标注"待确认"** — LLM 不确定的字段在卡片上标出，用户直接在框内填 |
| 错误用例 | **AI 全自动生成** — 不需要用户参与 |
| 正常用例 | 用户必须提供关键字段值（如用户名、密码） |
| 框架 | **保留 langchain**，去掉 LangGraph |
| 旧代码 | 清理原有三阶段 Pipeline + Agent 的冗余代码 |

---

## 📋 用户完整需求描述

1. **LLM 分析接口设计用例** — 用户上传 API 文档后，LLM 分析每个端点，设计测试用例
   - 入参中**不清楚的数据**（如登录用户名密码）→ 向用户索要
   - AI **能生成的内容**（如搜索关键词、创建的用户名）→ AI 自动生成
   - **不确定**的 → 在表格中标记"请确认"让用户决定

2. **列表式逐行展示** — LLM 输出内容以列表形式展示，每个测试用例是一张卡片
   - 用户在每个卡片的输入框中填写对应值
   - 值自动映射到接口请求数据体（body/params/headers）
   - 用户不填的字段 = 该字段数据为空/不发送

3. **测试用例设计标准**
   - **正常用例** → 需要用户输入关键值
   - **错误用例** → AI 自行生成（空值/非法值/边界值）

4. **输出 JSON 符合框架标准** — 生成的每一条请求必须匹配 `ApiRequest` 模型字段，可直接落库

5. **断言** — AI 生成简单断言（状态码、返回结构），不做高要求

---

## 🔧 架构方案

### 核心思路

去掉 **LangGraph StateGraph**（线性流程不需要图），保留 **langchain** 的 LLM 调用能力。
流程从 "LLM 决策下一步" 改为 **"确定性路由 + 单次 LLM 生成"**。

### 新旧流程对比

```
之前（复杂、低效）:
  upload → 老 Pipeline 全部跑完 → LLM 路由 → 逐个节点跑 → 对话式交互 → 保存

之后（简洁、可控）:
  Step 0: upload → parse → 存 raw_content
  Step 1: 配置项目
  Step 2: 调用 LLM 一次性分析+生成用例模板 → 渲染卡片列表 → 用户填写 → 确认保存
  Step 3: 结果页
```

### LLM 调用策略

**一次 LLM 调用完成全部工作**（非逐步），减少延迟和失败面：

```
输入: 端点定义 + tester.md 提示词 + 分类规则
输出: 完整的测试用例列表（含 _mode 标记）
```

---

## 📐 LLM 输出格式设计

LLM 输出的每个测试用例包含 `_mode` 标记，指示前端如何渲染：

```json
[
  {
    "name": "正常登录",
    "_case_type": "normal",
    "method": "POST",
    "url": "/api/login",
    "headers": {"Content-Type": "application/json"},
    "body": {
      "username": {"_mode": "user_input", "_label": "登录用户名", "value": null},
      "password": {"_mode": "user_input", "_label": "登录密码", "value": null}
    },
    "auth": {"type": "none"},
    "assertions": [
      {"type": "status_code", "expected": 200},
      {"type": "json_path", "json_path": "$.code", "expected": 0}
    ],
    "pre_request_script": "",
    "post_request_script": ""
  },
  {
    "name": "登录-用户名错误",
    "_case_type": "error",
    "method": "POST",
    "url": "/api/login",
    "body": {
      "username": {"_mode": "ai_generated", "value": "nonexistent_user"},
      "password": {"_mode": "user_input", "_label": "登录密码", "value": null}
    },
    "assertions": [{"type": "status_code", "expected": 401}]
  }
]
```

### `_mode` 三种取值：

| `_mode` | 含义 | 前端渲染 |
|---------|------|---------|
| `user_input` | 需要用户填写 | 黄色输入框，必填标记 |
| `ai_generated` | AI 自动生成 | 灰色文字，只读不可编辑 |
| `ask_user` | 不确定，请用户确认 | 输入框 + "?" 标记 + AI 建议值 |

### 前端输出 ApiRequest 时（保存时），将 `{"_mode": "user_input", "value": "admin"}` 转换为 `"admin"`

---

## 🗂️ 修改文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `apps/api_testing/ai_agent/generator.py` | **核心** — LLM 分析端点 + 生成带标记的测试用例模板 |
| `frontend/src/components/TestCaseCard.vue` | 可编辑测试用例卡片组件 |

### 重写/大幅修改文件

| 文件 | 改动 |
|------|------|
| `apps/api_testing/ai_agent/prompts.py` | **重写** — 去掉路由提示词，改为用例生成提示词 + tester.md 引用 |
| `apps/api_testing/ai_agent/schema.py` | **更新** — 增加 `_mode` 标记字段支持，增加 `frontend_to_db()` 转换函数 |
| `apps/api_testing/ai_agent/__init__.py` | 暴露 `generator.py` 的 AnalyzeGenerator |
| `apps/api_testing/views.py` | **重构 AIImportViewSet** — upload 不再跑老 pipeline；新增 `analyze` 端点；简化 `save` |
| `frontend/src/views/api-testing/AIImportWizard.vue` | **重写 Step 2** — 聊天组件替换为可编辑卡片列表 |
| `frontend/src/api/api-testing-import.js` | 替换 API 调用 |

### 删除文件

| 文件 | 说明 |
|------|------|
| `apps/api_testing/ai_agent/agent.py` | 去掉 LangGraph StateGraph — 不再需要图结构 |
| `apps/api_testing/ai_agent/tools.py` | 去掉 `@tool` — 不再需要 LangChain tool 抽象 |
| `apps/api_testing/ai_agent/persistence.py` | 去掉 DjangoCheckpointSaver — 不再需要 checkpoint |
| `apps/api_testing/ai_import_service.py` | 仅保留 `analyze_endpoints` 和其他有用函数，其余删除 |

### 保留代码（复用）

| 文件 | 保留内容 |
|------|---------|
| `apps/api_testing/doc_parser.py` | 全部保留 — 文档解析逻辑完整可用 |
| `apps/api_testing/ai_import_service.py` | 保留 `analyze_endpoints()`, `_hybrid_classify_params()`, `_extract_body_params()` 等分类逻辑 |
| `apps/api_testing/models.py` | AIImportTask 模型保持不变 |
| `docs/tester.md` | 测试用例编写规范，被 generator.py 引用 |

---

## 🧩 核心逻辑详述

### `generator.py` — AnalyzeGenerator

```python
class AnalyzeGenerator:
    """分析端点 -> LLM 生成带标记的测试用例模板"""
    
    def __init__(self, task_id: int):
        self.task_id = task_id
    
    def generate(self) -> List[dict]:
        """加载任务 -> 获取端点 -> 调 LLM -> 校验 -> 返回"""
        task = AIImportTask.objects.get(id=self.task_id)
        endpoints = task.parsed_endpoints
        
        # 构建 LLM 提示词
        prompt = self._build_prompt(endpoints)
        
        # 调用 LLM
        response = self._call_llm(prompt)
        
        # 解析 + 校验
        cases = json.loads(response)
        validated = validate_generator_output(cases)
        
        return validated
    
    def _build_prompt(self, endpoints) -> str:
        """结合 tester.md + 端点信息 + 分类规则 构建提示词"""
        ...
```

### 前端 Step 2 — 卡片列表渲染

```
┌─ Step 2: AI 分析结果 ──────────────────────────────────────┐
│                                                              │
│  ▸ POST /api/login (用户登录)                                │
│                                                              │
│  ┌─ 正常登录 ──── [正常] ──────────────────────────────────┐ │
│  │  URL: /api/login                  Method: POST           │ │
│  │  Headers: Content-Type: application/json                 │ │
│  │  Body:                                                   │ │
│  │    username:  [ ──────────────── ]  ← 必填 登录用户名    │ │
│  │    password:  [ ──────────────── ]  ← 必填 登录密码      │ │
│  │  断言: status_code = 200, $.code = 0                     │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ 登录-用户名错误 ─ [错误] ──────────────────────────────┐ │
│  │  Body:                                                   │ │
│  │    username:  nonexistent_user          (AI自动生成)     │ │
│  │    password:  [ ──────────────── ]  ← 必填 登录密码      │ │
│  │  断言: status_code = 401                                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ 登录-密码错误 ── [错误] ──────────────────────────────┐ │
│  │  Body:                                                   │ │
│  │    username:  [ ──────────────── ]  ← 必填 登录用户名    │ │
│  │    password:  wrong_password           (AI自动生成)       │ │
│  │  断言: status_code = 401                                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ 登录-用户名为空 ─ [错误] ────────────────────────────┐ │
│  │  Body:                                                   │ │
│  │    username:  (空)                     (AI自动生成)       │ │
│  │    password:  [ ──────────────── ]  ← 必填 登录密码      │ │
│  │  断言: status_code = 400                                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  [← 上一步]                              [确认保存 →]       │
└──────────────────────────────────────────────────────────────┘
```

### 视图层 API 变更

| 端点 | 现状 | 变更后 |
|------|------|--------|
| `POST /upload/` | 跑老 pipeline | 仅解析文档，存 raw_content + parsed_endpoints |
| `POST /configure/` | 保存项目配置 | 不变 |
| `POST /analyze/` | **不存在** | **新增** — 调 LLM 生成测试用例模板 |
| `GET /{id}/agent-state/` | 获取 Agent 状态 | 删除 |
| `POST /{id}/agent-reply/` | Agent 回复 | 删除 |
| `POST /answers/` | 提交回答 | 删除 |
| `GET /questions/` | 获取问题 | 删除 |
| `GET /preview/` | 预览 | 删除 |
| `POST /save/` | 保存 | **简化** — 接收前端填完的用例列表直接落库 |

---

## ✅ 验证方案

1. **上传 Swagger/OpenAPI 文档** → 确认解析正确、端点信息提取完整
2. **点击"AI 分析"** → 调用 `POST /analyze/` → 检查 LLM 返回的模板格式
   - 正常用例标记 `user_input`
   - 错误用例标记 `ai_generated`
   - 不确定字段标记 `ask_user`
3. **前端渲染** → 每个用例一张卡片，输入框正确显示
4. **填写字段** → 在输入框中填写值 → 确认值正确映射到 body/params/headers
5. **确认保存** → 点击保存 → 检查 `ApiRequest` 表数据：
   - 字段类型正确（headers 是 dict，body 是 dict 等）
   - 用户未填的字段为空/不出现
   - AI 生成的字段有正确的值
6. **运行保存的请求** → 使用 API 测试功能发送，验证可执行

---

## ⚠️ 注意事项

1. **`AIModelConfig` 缺少 `api_import` 角色** — 需要在模型的 `ROLE_CHOICES` 中新增 `('api_import', 'API导入')`，以便为导入功能配置专用模型
2. **LLM 输出必须严格受 `validate_generator_output()` 约束** — LLM 可能格式出错，校验层要在 `generator.py` 内部处理并触发重试
3. **`ask_user` 字段处理** — 用户如果填写了值，用用户的值；如果没填，用 AI 建议值
4. **前端 `TestCaseCard.vue` 需要递归渲染** — body/headers 可以是嵌套结构，输入框要能定位到正确的 JSON Path
5. **大文档分页** — 如果端点数量 > 20，LLM 一次输出可能超 token 限制，需要分批次生成
