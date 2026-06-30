# AI 驱动的 API 测试用例导入 — 设计文档

## 概述

为 TestHub API 测试模块增加 AI 驱动的批量导入功能。用户上传 JSON / YAML 格式的 API 文档（支持 Swagger 2.0 / OpenAPI 3.0 / Postman Collection v2.1 / HAR 1.2 四种规范）后，系统自动解析端点、用 AI 分析参数类型、向用户提问缺失信息（认证方式、业务参数值、环境变量），最后批量生成完整的 API 测试用例并存入数据库。

## 背景

TestHub 现有 API 测试模块（`apps/api_testing/`）支持手动创建 ApiProject → ApiCollection → ApiRequest，以及手动执行请求。但实际项目中 API 文档往往已有（Swagger/Postman），手动逐条录入重复劳动量大且容易出错。

参考主流 API 管理工具（Postman Import、Apifox 文档导入）的能力，本设计实现类似功能：上传文档 → AI 辅助补全 → 批量入库。不同之处在于引入 LLM 对参数进行智能分类，并对不确定的业务参数值主动向用户提问，而非全部自动填充或全部留给用户手动填写。

## 关键设计决策

1. **文档格式**：支持 JSON / YAML 两种文件格式，覆盖 Swagger 2.0 / OpenAPI 3.0 / Postman Collection v2.1 / HAR 1.2 四种 API 规范，后端自动检测规范类型
2. **参数分类**：混合模式 — 启发式规则（`page`→auto, `Authorization`→manual, `id`→context_ref）+ LLM 兜底不确定参数
3. **提问粒度**：一次性汇总提问 — 所有 manual 参数合并去重后统一列出，而非逐端点弹窗
4. **AI 分析时机**：同步在 `upload` action 中执行（上传→parse→analyze→questions→返回）。适合 MVP，后续可改为 Celery 异步
5. **环境变量**：AI 发现硬编码值时问用户指定变量 key 名，URL/参数中生成 `{{var_name}}` 模板语法
6. **并发安全**：`select_for_update()` 在 answers/save 端点保护任务状态

## 用户流程

```
上传文档(JSON/YAML) → 选择项目/集合 → AI 分析端点 → 问题列表渲染 → 用户填写 → 生成并预览 → 保存到 DB
      Step 1             Step 2         Step 3         Step 4          Step 5      Step 6         Step 7
```

## 模块设计

### 模块 1：AIImportTask 模型

**文件**: `apps/api_testing/models.py`

在 `OperationLog` 类后新增 `AIImportTask` 模型，跟踪整个导入工作流的七种状态：

```python
class AIImportTask(models.Model):
    IMPORT_STATUS_CHOICES = [
        ('uploading', '上传中'), ('parsing', '解析文档中'),
        ('analyzing', 'AI分析中'), ('waiting_user', '等待用户配置'),
        ('generating', 'AI生成中'), ('completed', '已完成'), ('failed', '失败'),
    ]
```

核心字段全部使用 JSONField 存储中间数据，无需额外的关联表：

| 字段 | 类型 | 用途 |
|------|------|------|
| `raw_content` | JSONField | 原始文档内容 |
| `parsed_endpoints` | JSONField | `ParsedEndpoint[]` 归一化端点列表 |
| `ai_classification` | JSONField | 分类结果 `{auto:[], manual:[], context_ref:[]}` |
| `ai_questions` | JSONField | AI 问题列表 `AIQuestion[]` |
| `user_answers` | JSONField | 用户回答 dict |
| `environment_vars` | JSONField | 环境变量映射 |
| `generated_summary` | JSONField | 生成结果摘要 |

**状态机流转**：`uploading → parsing → analyzing → waiting_user → generating → completed`（异常时任意状态 → failed）

`created_by` 外键关联 User，`project` 和 `collection` 外键关联 ApiProject / ApiCollection（SET_NULL），支持导入到指定项目集合。

### 模块 2：多格式文档解析器

**文件**: `apps/api_testing/doc_parser.py`（新建）

接收 JSON / YAML 文件解析后的 Python dict，将四种 API 文档格式归一化为统一的 `ParsedEndpoint[]` 结构。

**核心数据结构**：

```python
class ParsedEndpoint(TypedDict, total=False):
    path: str           # /api/users/{id}
    method: str         # GET / POST / PUT / DELETE / PATCH
    summary: str        # 接口名称
    description: str
    tags: list[str]     # 分组标签
    operation_id: str
    parameters: list[ParsedParameter]
    request_body: Optional[dict]
    responses: dict
    security: list[dict]
    deprecated: bool

class ParsedParameter(TypedDict, total=False):
    name: str
    location: str       # query / header / path / body
    type: str           # string / integer / boolean / array / object
    description: str
    required: bool
    default: Any
    example: Any
    enum: list[Any]
```

**解析流程**：

```
parse_document(content: dict) → list[ParsedEndpoint]
  │
  ├─ detect_format(content) → 'swagger2'|'openapi3'|'postman'|'har'
  │   基于顶层 key 检测：swagger→swagger2, openapi→openapi3,
  │   info.schema→postman, log.entries→har
  │
  └─ 委托对应解析器
       ├─ _parse_swagger2    — 遍历 paths, 解析 parameters/ responses
       ├─ _parse_openapi3    — 同上，额外处理 $ref 引用、oneOf
       ├─ _parse_postman     — 遍历 item 递归，解析 URL 变量、header、body
       └─ _parse_har         — 遍历 log.entries，从 request 字段提取
```

**$ref 解析**：使用 `prance` 库（`prance[openapi]`），fallback 为 `_simple_dereference()` 递归替换 `$ref` JSON 指针。

**格式检测细节**：
- `swagger` key → Swagger 2.0
- `openapi` key → OpenAPI 3.0+
- `info.schema` 存在于根级 → Postman Collection
- `log.entries` 存在于根级 → HAR 1.2

### 模块 3：AI 导入服务

**文件**: `apps/api_testing/ai_import_service.py`（新建）

三阶段管线：参数分类 → 问题生成 → 请求数据生成。

**Phase 1 — 参数分类**：

```python
analyze_endpoints(endpoints) → Classification
```

混合策略 — 启发式规则 + LLM 兜底：
- `_AUTO_PARAM_PATTERNS`：`page`, `page_size`, `per_page`, `timestamp`, `_t`, `format`, `locale`, `callback` → **auto**（无需用户输入，AI 自动生成值）
- `_CONTEXT_REF_PATTERNS`：`id`, `token`, `session`, `name`, `key` → **context_ref**（需用户指定存在的资源）
- 其余参数 → 交由 `_llm_classify_uncertain_params()` 用 LLM 判断
- `Authorization`, `X-API-Key` 等认证 header → **manual**（需用户提供值）

返回结构：
```python
{
    "endpoint_count": 10,
    "total_params": 45,
    "auto_params": 12,
    "manual_params": 28,
    "context_ref_params": 5,
    "classification": {
        "GET /api/users": {"auto": ["page", "format"], "manual": ["Authorization"], "context_ref": ["id"]},
        # ...
    }
}
```

**Phase 2 — 问题生成**：

```python
generate_questions(classification, endpoints) → list[AIQuestion]
```

聚合所有 manual 参数按类别合并为四种问题类型：

| 类别 | field_type | 内容 |
|------|-----------|------|
| `url_domain` | `string` | 检测到的不同域名，问 base URL |
| `auth` | `select` | 认证 header，问认证方式（下拉选择：Bearer Token / API Key / Basic Auth / 自定义） |
| `param_value` | `multi_param` | 业务参数表格（每行：参数名 + 方法tag + 端点 + 输入框） |
| `env_var` | `env_var_mapping` | 环境变量映射（动态增减行：原始值 → `{{变量名}}`） |

```python
class AIQuestion(TypedDict):
    id: str               # q_1, q_2, ...
    category: str         # env_var / auth / param_value / url_domain
    title: str
    description: str
    field_type: str       # string / select / multi_param / env_var_mapping
    options: list[dict]
    related_endpoints: list[str]
    related_params: list[str]
```

**Phase 3 — 请求数据生成**：

```python
generate_requests(endpoints, classification, user_answers, env_vars) → list[dict]
```

对每个 endpoint：
1. auto 参数 → LLM 生成典型值
2. manual 参数 → 从 `user_answers` 获取值
3. context_ref 参数 → 保持 `{{var}}` 模板语法
4. 替换 URL/参数中的 `{{var}}` 占位符

返回可直接传给 `ApiRequest.objects.create()` 的 dict 列表。

### 模块 4：后端 API 端点

**文件**: `apps/api_testing/views.py`、`apps/api_testing/serializers.py`、`apps/api_testing/urls.py`

**视图集**：`AIImportViewSet(viewsets.GenericViewSet)`，9 个端点：

| HTTP | 端点 | 功能 | 状态流转 |
|------|------|------|---------|
| POST | `/upload/` | 上传文档，同步解析+AI分析 | 上传中 → 等待用户配置 |
| GET | `/{id}/` | 任务详情 | — |
| GET | `/{id}/questions/` | AI 问题列表 + 分类统计 | — |
| POST | `/{id}/configure/` | 选择项目 + 组织结构 | — |
| POST | `/{id}/answers/` | 提交回答，触发 AI 生成 | 等待用户配置 → 生成中 → 完成 |
| GET | `/{id}/preview/` | 预览生成的请求数据 | — |
| POST | `/{id}/save/` | 保存到数据库 | 完成 → （创建 ApiRequest） |
| GET | `/{id}/logs/` | SSE 流式进度推送 | — |
| GET | `/list_tasks/` | 任务历史列表 | — |

**序列化器**：

| 序列化器 | 字段 | 用途 |
|---------|------|------|
| `AIImportTaskSerializer` | 所有模型字段（parsed_endpoints 等只读） | 任务详情返回 |
| `AIImportUploadSerializer` | `file`（JSON/YAML 文件）, `project_id` | 文件上传，后端用 `PyYAML` 解析 YAML，`json.loads` 解析 JSON |
| `AIImportConfigureSerializer` | `project_id`, `auto_structure`, `target_collection_id` | 项目配置 |
| `AIImportAnswersSerializer` | `user_answers`, `environment_vars` | 用户回答提交 |

**关键实现细节**：
- `save` action 按 `auto_structure` 分支：True 时按 tags 自动创建 ApiCollection 并分组；False 时全部导入到 `target_collection_id` 指定集合
- `logs` action 用 `StreamingHttpResponse` + DB polling 实现 SSE 进度推送
- `answers` 和 `save` 端点使用 `select_for_update()` 防止同一任务的并发操作

### 模块 5：前端向导组件

**文件**: `frontend/src/views/api-testing/AIImportWizard.vue`（新建）

六步骤向导组件：

| Step | 标题 | 内容 | 交互 |
|------|------|------|------|
| 0 | 上传文档 | `el-upload` drag 模式，限 `.json` / `.yaml` / `.yml` 后缀 | 上传后自动解析，检测文档规范类型（Swagger / OpenAPI / Postman / HAR）+ 显示端点数 |
| 1 | 项目配置 | 选择项目 + 组织结构切换 | `el-tree-select` 选集合，auto_structure 开关 |
| 2 | AI 分析 | 加载动画 + 端点统计卡片 | 自动轮转，不可手动跳过 |
| 3 | 问题列表 | 按类别渲染问题卡片 | `string`→输入框 / `select`→下拉 / `multi_param`→表格 / `env_var_mapping`→动态行 |
| 4 | 生成 | SSE 进度条 + 实时计数 | 自动轮转到结果页面 |
| 5 | 结果 | 成功数 + 查看/继续按钮 | 跳转到 InterfaceManagement 页面 |

**问题列表渲染映射**：
- `field_type: "string"` → `<el-input>` 输入框
- `field_type: "select"` → `<el-select>` 下拉框（选项来自 AI 分析的 `options` 字段）
- `field_type: "multi_param"` → `<el-table>` 表格（每行：参数名 + 请求方法标签 + 端点路径 + 输入框）
- `field_type: "env_var_mapping"` → 动态增减行（原始值 → `{{变量名}}` 映射）

**AI 分析页端点统计数据卡片**：显示总端点数、total/auto/manual/context_ref 参数分类计数，使用 Element Plus 统计卡片组件。

### 模块 6：前端 API + 路由 + i18n

**文件**: `frontend/src/api/api-testing-import.js`（新建）

导出 9 个函数：
- `uploadDocument(file)` — POST `/upload/`
- `getTaskDetail(id)` — GET `/{id}/`
- `getTaskQuestions(id)` — GET `/{id}/questions/`
- `configureTask(id, data)` — POST `/{id}/configure/`
- `submitAnswers(id, data)` — POST `/{id}/answers/`
- `previewRequests(id)` — GET `/{id}/preview/`
- `saveRequests(id)` — POST `/{id}/save/`
- `subscribeLogs(id, handlers)` — SSE 订阅 `/{id}/logs/`
- `listTasks(params)` — GET `/list_tasks/`

复用现有 `request` 包装器（基于 axios）。SSE 订阅使用 `EventSource` 或 fetch streaming。

**路由**：`/api-testing/ai-import` → `AIImportWizard.vue`

```javascript
{
  path: 'ai-import',
  name: 'ApiAIImport',
  component: () => import('@/views/api-testing/AIImportWizard.vue'),
}
```

**i18n**：在 `zh-cn/api-testing.js` 和 `en/api-testing.js` 中新增 `aiImport` 区域，包含向导各步骤标题、按钮文案、状态标签、问题类别标题等约 40 个 key。

### 模块 7：集成

**文件**: `frontend/src/views/api-testing/InterfaceManagement.vue`

接口管理页面操作列"更多"下拉菜单增加"AI 导入"入口，点击跳转到 `/api-testing/ai-import`。

**文件**: `requirements.txt`

新增依赖：`prance[openapi]`（OpenAPI $ref 解析）、`PyYAML`（YAML 文件解析）。

**文件**: `apps/api_testing/tests/`

三个测试文件共 190 个测试用例：
- `test_ai_import_parser.py` — 四种格式解析 + $ref 解析 + 格式检测 + 边界情况
- `test_ai_import_service.py` — 启发式分类 + LLM 分类 + 问题生成 + 请求生成
- `test_ai_import_views.py` — 上传→配置→问答→生成→保存 全流程 + 权限 + 并发安全

## 影响范围汇总

| 文件 | 改动类型 |
|------|---------|
| `apps/api_testing/models.py` | +AIImportTask 模型 |
| `apps/api_testing/doc_parser.py` | 新建 — 四格式解析器 |
| `apps/api_testing/ai_import_service.py` | 新建 — 三阶段 AI 分析服务 |
| `apps/api_testing/serializers.py` | +4 序列化器 |
| `apps/api_testing/views.py` | +AIImportViewSet（9 端点） |
| `apps/api_testing/urls.py` | +router.register `ai-import` |
| `apps/api_testing/tests/test_ai_import_parser.py` | 新建 |
| `apps/api_testing/tests/test_ai_import_service.py` | 新建 |
| `apps/api_testing/tests/test_ai_import_views.py` | 新建 |
| `frontend/src/views/api-testing/AIImportWizard.vue` | 新建 — 六步骤向导 |
| `frontend/src/views/api-testing/InterfaceManagement.vue` | +AI 导入入口 |
| `frontend/src/api/api-testing-import.js` | 新建 |
| `frontend/src/router/index.js` | +AiImport 路由 |
| `frontend/src/locales/lang/zh-cn/api-testing.js` | +aiImport i18n |
| `frontend/src/locales/lang/en/api-testing.js` | +aiImport i18n |
| `requirements.txt` | +prance[openapi], +PyYAML |

## 不变的设计原则

1. **参数分类是辅助而非决策** — AI 的分类结果用户可以覆盖（问题列表填写阶段）。auto/manual/context_ref 的划分只是为了优化 UI 交互，用户最终可以用任意值填充任意参数
2. **保持现有数据结构** — 生成结果存入已有的 `ApiCollection` / `ApiRequest` 模型，不修改现有模型字段
3. **同步分析，后续可异步** — MVP 采用同步方式（上传请求中完成 AI 分析），后续可改为 Celery 后台任务，只需将 `upload` action 拆分为"创建任务"和"执行分析"两步
4. **同一 AI 配置接口** — 复用已有的 `AIModelConfig` 和 `AIModelService`，提示词使用 `tester.md` / `tester_pro.md` 的格式风格
5. **SSE 进度推送** — 生成阶段使用 Server-Sent Events 实时推送进度，复用需求分析模块的 SSE 模式

## QA 验证标准

### 测试用例 1：文档解析

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1.1 | 准备一个 Swagger 2.0 JSON 文件（含 3 个端点，含 path/query 参数） | 文件准备完毕 |
| 1.2 | 通过向导 Step 0 上传 `.json` 文件 | 自动检测为 Swagger 2.0，显示 3 个端点，可进入下一步 |
| 1.3 | 重复 1.1-1.2 使用 OpenAPI 3.0 JSON 文件 | 正确识别为 OpenAPI 3.0，端点数据完整 |
| 1.4 | 重复 1.1-1.2 使用 Postman Collection v2.1 JSON 文件 | 正确识别为 Postman，端点数据完整 |
| 1.5 | 重复 1.1-1.2 使用 HAR JSON 文件 | 正确识别为 HAR，端点数据完整 |
| 1.6 | 上传 YAML 格式（`.yaml` / `.yml`）的 OpenAPI 文件 | 正确解析 YAML，结果与 JSON 版本一致 |
| 1.7 | 上传包含 `$ref` 引用的 Swagger JSON 文件 | `$ref` 被正确解析展开，参数列表完整 |
| 1.8 | 上传 `.json` 后缀但内容是 YAML 的文件，或反之 | 以文件实际内容为准，不依赖后缀判断 |

### 测试用例 2：项目配置

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 2.1 | 进入 Step 1，选择一个已有项目 | 项目选择器工作正常 |
| 2.2 | 切换组织结构为"按标签自动创建集合" | auto_structure 开关切换为 true |
| 2.3 | 切换为"导入到指定集合"，从树形选择中选择集合 | 集合选择器展开显示项目下的集合树 |
| 2.4 | 点击"开始分析" | 进入 Step 2 加载动画 |

### 测试用例 3：AI 分析 + 问题列表

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 3.1 | 上传含认证 header 和分页参数的文档 | AI 分析完成后自动进入 Step 3 |
| 3.2 | 确认 Step 3 显示了分类统计（auto/manual/context_ref 数量） | 统计卡片显示正确 |
| 3.3 | URL 域名问题：输入 Base URL | string 输入框正常 |
| 3.4 | 认证问题：下拉选择认证方式 | select 下拉框正常，选项来自 AI |
| 3.5 | 业务参数表格：为每个参数填写值 | multi_param 表格每行有输入框 |
| 3.6 | 环境变量映射：添加一行"原始值 → {{变量名}}" | env_var_mapping 动态行可增删 |

### 测试用例 4：生成与保存

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 4.1 | 在 Step 3 填写完成后点击"生成" | 进入 Step 4，进度条实时更新 |
| 4.2 | 等待生成完成 | 自动进入 Step 5，显示成功数 |
| 4.3 | 点击"查看详情" | 跳转到接口管理页面，显示新创建的请求 |
| 4.4 | 验证数据库 | ApiCollection 和 ApiRequest 数据完整 |
| 4.5 | 点击某个生成的请求，尝试执行 | 请求可正常发送，参数值正确填充 |

### 测试用例 5：边界与异常

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 5.1 | 上传空文档（无 endpoint） | 提示"未检测到任何 API 端点" |
| 5.2 | 上传非支持格式的文件（如图片） | 返回格式错误提示 |
| 5.3 | 上传超过 10MB 的文件 | 返回文件大小限制错误 |
| 5.4 | 选择"导入到指定集合"但不选集合 | 提示"请选择目标集合" |
| 5.5 | 在生成阶段多次点击保存 | select_for_update 防止重复创建 |

### QA 准入/准出标准

**准入条件**：
- 所有后端代码已提交，数据库迁移已执行（`python manage.py migrate`）
- 前端代码已构建（`npm run build` 通过）
- 有至少一个测试文件（Swagger 2.0 / OpenAPI 3.0 / Postman / HAR）

**准出条件**：
- TC1（文档解析）: ✅ 通过 — 四格式解析 + YAML + $ref 均可正常工作
- TC2（项目配置）: ✅ 通过 — 选择和树形结构正常
- TC3（AI 分析）: ✅ 通过 — 四种问题类型渲染正常，可填写
- TC4（生成保存）: ✅ 通过 — SSE 进度 + 数据库写入正常
- TC5（边界异常）: ✅ 通过 — 空文档/格式错误/并发保护均符合预期
