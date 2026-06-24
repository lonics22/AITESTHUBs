# LVM 视觉模型支持 — 设计文档

## 概述

为 TestHub AI 需求分析-用例生成功能增加 LVM（Large Vision Model，视觉大模型）支持。使用户在粘贴 PRD 流程图、UI 截图等图片后，系统能自动调用视觉模型识别图片内容，再将文本描述与原始需求文本合并，送入 LLM 生成测试用例。

## 背景

当前 TestHub 仅支持 LLM（文本大模型）。大量 PRD 包含流程图、UI 界面截图等视觉信息，纯文本提取会丢失关键内容。参考项目 D:\AI\AITest 已实现 LVM+LLM 双模式，本设计借鉴其成熟方案，同时贴合 TestHub 现有架构。

## 关键设计决策

1. **LVM 配置位置**：系统级 `AIModelConfig` 增加 `vision` role（方案 A）
2. **图片交互方式**：Ctrl+V 粘贴图片 → 自动上传 → 在光标处插入 `![描述](url)`，预览区渲染实际图片
3. **处理流程**：全自动管线，LVM 预处理对用户无感（方案 A）

## 模块设计

### 模块 1：AIModelConfig — 增加 vision role

**文件**: `apps/requirement_analysis/models.py`

```python
# ROLE_CHOICES 增加
ROLE_CHOICES = [
    ('writer', '测试用例编写专家'),
    ('reviewer', '测试评审专家'),
    ('browser_use_text', 'Browser Use - 文本模式'),
    ('vision', '视觉模型（LVM）'),  # 新增
]
```

- 现有字段（`api_key`、`base_url`、`model_name`、`max_tokens`、`temperature`）完全够用，不新增字段
- `AIModelConfigViewSet` CRUD 逻辑通用，无需修改
- `AIModelService.call_openai_compatible_api()` 使用 OpenAI 兼容格式，LVM 通过同样的接口调用（`messages` 中包含 `image_url` 类型内容）

### 模块 2：前端图片粘贴与内联显示

**文件**: `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue`

**交互流程**：
1. 用户 Ctrl+V 粘贴截图
2. 前端检测剪贴板 `image/png`、`image/jpeg` 等类型
3. 自动上传到 `POST /api/requirement-analysis/upload-image/`
4. 在光标位置插入 `![图片描述](url)`
5. 右侧/下方 Markdown 预览区渲染出实际图片（修改 `formatMarkdown()` 支持 `![alt](url)` → `<img>`）

**具体修改**：
- textarea 增加 `@paste="handlePaste"` 事件处理
- `handlePaste(event)` 方法：检查 `event.clipboardData.files` 中是否有图片 → 调上传 API → 插入文本
- `formatMarkdown()` 增加图片渲染：`/!\[(.*?)\]\((.*?)\)/g` → `<img src="$2" alt="$1" class="preview-image">`
- 限制最多 20 张图片，单张最大 10MB

**新增 API**：
```
POST /api/requirement-analysis/upload-image/
Request: multipart/form-data { file: Binary }
Response: { "id": 1, "url": "/media/requirement_images/2026/06/abc123.png", "filename": "image.png" }
```

### 模块 3：图片上传后端

**文件**: `apps/requirement_analysis/models.py`（新增模型）

```python
class RequirementImage(models.Model):
    file = models.ImageField(upload_to='requirement_images/%Y/%m/')
    filename = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # LVM 识别后的描述，可留空
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = 'requirement_images'
```

**文件**: `apps/requirement_analysis/views.py`（新增视图）

- `UploadImageView` — 接收图片文件，校验格式（png/jpg/gif/webp），保存，返回 URL
- 注册路由 `POST /api/requirement-analysis/upload-image/`

**文件**: `apps/requirement_analysis/urls.py`

```
path('upload-image/', views.UploadImageView.as_view(), name='upload-image'),
```

### 模块 4：LVM 图片分析管线

**文件**: `apps/requirement_analysis/models.py` — `AIModelService` 新增方法

```python
@staticmethod
async def preprocess_images(requirement_text: str, lvm_config: AIModelConfig) -> str:
    """扫描文本中的 ![alt](url) 标记，并发调用 LVM 识别，替换为文字描述"""
```

**流程**：
1. 正则提取所有 `![alt](url)` 标记
2. 对每个 URL：下载（本地 media 路径则直接读取）→ 转为 base64
3. 调用 LVM（OpenAI vision 格式）：
   ```python
   messages = [{"role": "user", "content": [
       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_data}"}},
       {"type": "text", "text": vision_prompt}
   ]}]
   ```
4. 使用 `ThreadPoolExecutor(max_workers=3)` 并发处理（参考项目已验证此方案稳定）
5. 替换原标记为 `> 流程图描述：...` 或 `> 原型图界面描述：...`
6. 图片级缓存：同一 URL 在同一任务中不重复调用 LVM（内存 dict 缓存）

**LVM 提示词**（从 PromptConfig `prompt_type='vision'` 获取，默认值）：
```
任务目标：这个图片来自软件开发需求文档。请识别它是流程图还是界面原型图，
并对它的内容进行文字描述。

输出要求：只需要输出类型和对它内容的描述，不要输出其他无关内容和评价。
输出的开头为："原型图界面描述：" 或 "流程图描述："
```

**异常处理**：
- 单张图片失败 → 保留原 `![alt](url)`，记录日志，不阻断整体流程
- 所有图片失败 → 降级为纯 LLM 流程
- LVM 模型未配置 → 跳过预处理（`preprocess_images` 直接返回原文），不报错

### 模块 5：生成管线集成

**文件**: `apps/requirement_analysis/views.py` — `TestCaseGenerationTaskViewSet`

在 `generate/` 入口中，开始生成流程前增加 LVM 预处理步骤：

```
当前流程：
  用户提交 → 创建任务 → 调用 LLM writer → (reviewer) → 完成

修改后流程：
  用户提交 → 创建任务 → LVM 预处理 → 调用 LLM writer → (reviewer) → 完成
```

具体代码位置：在 `start_generation_task()` 中，读取活跃的 `vision` 角色 `AIModelConfig` 和 `vision` 类型 `PromptConfig`。如果两者都存在，则在调用 `generate_test_cases_stream` 前执行 `preprocess_images(task.requirement_text, lvm_config)`，将处理后的文本更新到 `task.requirement_text`。

**无 LVM 配置时的行为**：`preprocess_images` 检测不到活跃的 vision 模型/提示词时，直接返回原文。这是显式支持的降级路径，用户可正常使用现有功能。

### 模块 6：PromptConfig — 增加 vision 类型

**文件**: `apps/requirement_analysis/models.py`

```python
PROMPT_CHOICES = [
    ('writer', '用例编写提示词'),
    ('reviewer', '用例评审提示词'),
    ('vision', '图片分析提示词'),  # 新增
]
```

**文件**: `frontend/src/locales/lang/zh-cn/requirement.js` — 在 promptConfig 区域增加

```javascript
imageAnalyzer: '图片分析',
visonPrompt: '图片分析提示词',
```

**文件**: `frontend/src/views/requirement-analysis/PromptConfig.vue`

- 类型筛选下拉框增加「图片分析」选项
- 表单中 prompt_type 选择器增加对应选项
- 默认值加载逻辑增加 `vision` 类型

### 模块 7：配置检查与引导弹窗

**文件**: `apps/requirement_analysis/views.py` — `ConfigStatusViewSet.check`

响应扩展新增两个字段：

```python
config_status = {
    # 原有字段保持不变...
    'lvm_model': {
        'configured': True/False,
        'enabled': True/False,
        'name': '模型名称' or None,
        'id': 1 or None,
        'required': False,  # LVM 可选
    },
    'vision_prompt': {
        'configured': True/False,
        'enabled': True/False,
        'name': '提示词名称' or None,
        'id': 1 or None,
        'required': False,
    },
}
```

`overall_status` 逻辑：
- `writer_model` + `writer_prompt` + `generation_config` 都就绪 → `"enabled"`（core 条件）
- LVM 未配置 → 不影响 overall_status（LVM 是增强功能，非必需）

**文件**: `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue`

- 配置引导弹窗在「提示词配置」分组下增加一行「图片分析」状态显示
- 在「模型配置」分组下增加一行「视觉模型」状态显示
- 状态标识规则与现有逻辑一致：绿色✓已配置启用 / 黄色○已配置未启用 / 红色✗未配置

**文件**: `frontend/src/locales/lang/zh-cn/requirement.js`

```javascript
configGuide: {
    // 新增
    imageAnalysis: '图片分析',
    visionModel: '视觉模型',
    visionLvm: '视觉模型(LVM)',
    visionHint: '推荐配置视觉模型以识别需求文档中的图片',
}
```

## 影响范围汇总

| 文件 | 改动类型 |
|------|---------|
| `apps/requirement_analysis/models.py` | +1 ROLE_CHOICE, +1 PROMPT_CHOICE, +1 RequirementImage 模型, +1 preprocess_images 方法 |
| `apps/requirement_analysis/views.py` | +1 UploadImageView, 修改 start_generation_task 增加 LVM 步骤, 扩展 config check |
| `apps/requirement_analysis/serializers.py` | 不变（通用 CRUD 无需改动） |
| `apps/requirement_analysis/urls.py` | +1 路由 upload-image |
| `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue` | +paste 事件处理, +图片上传, formatMarkdown 增加 img 渲染, config guide 扩展 |
| `frontend/src/views/requirement-analysis/PromptConfig.vue` | 筛选/表单增加 vision 选项 |
| `frontend/src/views/requirement-analysis/AIModelConfig.vue` | 角色筛选增加「视觉模型」选项 |
| `frontend/src/locales/lang/zh-cn/requirement.js` | +i18n key |
| `frontend/src/locales/lang/en/requirement.js` | +i18n key |
| `frontend/src/api/requirement-analysis.js` | +1 uploadImage API 方法 |

## 不变的设计原则

1. **LVM 是增强功能，非强制依赖** — 没有 LVM 配置时，现有功能完全不受影响
2. **保持现有 UI 风格** — 卡片式布局、Element Plus 组件、渐变配色、icon 风格一致
3. **同一 OpenAI 兼容接口** — LVM 与 LLM 共用同一套 `call_openai_compatible_api` 调用，消息格式升级为包含 `image_url` 的 multimodal 格式
4. **流式体验不变** — LVM 预处理在生成前同步完成，SSE 流式推送不受影响

## QA 验证标准

### 测试凭证

- **提供商**: 智谱 (Zhipu) — 已在 MODEL_CHOICES 中支持
- **API 地址**: `https://open.bigmodel.cn/api/paas/v4/chat/completions`
- **API Key**: `0f4558f6e2c04fdbbf1338eee081f60e.g3wsymGI0KntxyoG`
- **模型名称**: `glm-5v-turbo`

### 测试用例 1：LVM 配置与连接测试

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 1.1 | 进入「AI 模型配置」页面，点击添加配置 | 弹出配置表单 |
| 1.2 | 填写：名称="智谱LVM"、模型类型="智谱"、角色="视觉模型(LVM)"、API Key、Base URL、模型名称=`glm-5v-turbo`，其余默认 | 表单填写正常 |
| 1.3 | 点击「测试连接」 | 弹出"连接成功"提示，说明 API 密钥和端点有效 |
| 1.4 | 点击「获取模型列表」 | 能获取到模型列表或返回当前模型名称 |
| 1.5 | 保存并启用 | 配置卡片显示绿色启用状态 |

### 测试用例 2：图片分析提示词配置

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 2.1 | 进入「提示词配置」页面，点击添加配置 | 弹出配置表单 |
| 2.2 | 填写：名称="默认图片分析"、提示词类型="图片分析" | 表单填写正常 |
| 2.3 | 点击「加载默认提示词」或手动填入图片分析提示词 | 内容填充正常 |
| 2.4 | 保存并启用 | 配置卡片显示绿色启用状态 |

默认图片分析提示词：
```
任务目标：这个图片来自软件开发需求文档。请识别它是流程图还是界面原型图，
并对它的内容进行文字描述。

输出要求：只需要输出类型和对它内容的描述，不要输出其他无关内容和评价。
输出的开头为："原型图界面描述：" 或 "流程图描述："
```

### 测试用例 3：页面图片粘贴与展示

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 3.1 | 打开「需求分析」页面 | 页面加载正常，配置检查通过 |
| 3.2 | 在需求描述文本框中输入标题和部分文字 | 文本输入正常 |
| 3.3 | 截取一张流程图或 UI 截图（使用微信截图/QQ截图/系统截图），Ctrl+V 粘贴到文本框 | 图片自动上传，光标位置出现 `![图片描述](url)` |
| 3.4 | 继续输入文字，再次粘贴另一张图片 | 第二张图片也正常插入，位置正确 |
| 3.5 | 点击「生成测试用例」 | 进入生成流程 |
| 3.6 | 观察实时流式内容 | 生成内容中包含对图片中流程图/UI 的描述性文字（如"流程图描述：用户登录流程包含账号输入、密码校验..."） |

**测试图片建议**：使用一张包含登录流程图的 PRD 截图，或一张简单的 UI 界面截图（如搜索页面、表单页面）。

### 测试用例 4：LVM → LLM 全链路

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 4.1 | 在需求描述中粘贴一张含流程图的 PRD 截图 | 图片正常显示在预览区 |
| 4.2 | 填写完整需求描述（标题+文字+图片） | 信息完整 |
| 4.3 | 点击「生成测试用例」 | 任务创建成功，进入生成步骤 |
| 4.4 | 观察步骤进度：需求分析(1) → 用例编写(2) → 用例评审(3) → 生成完成(4) | LVM 预处理在步骤1-2之间完成，用户无感 |
| 4.5 | 检查生成的测试用例中是否包含了与图片内容相关的用例 | 如：PRD 图片是登录流程图，应生成"登录功能"相关测试用例 |
| 4.6 | 点击「下载 Excel」 | Excel 输出正常，包含完整的测试用例表格 |

### 测试用例 5：降级与异常处理

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 5.1 | 停用 LVM 配置，仅保留 LLM 配置 | 配置引导弹窗显示 LVM 未配置（黄色/红色提示），但整体功能可用 |
| 5.2 | 提交带图片的需求文本 | LVM 预处理跳过，直接调用 LLM，LLM 看到的是原始 `![alt](url)` 文本 |
| 5.3 | 测试单张图片超过 10MB 的上传 | 返回错误提示，不阻塞页面 |
| 5.4 | 测试不支持的格式（如 .psd）粘贴 | 被忽略或提示不支持 |
| 5.5 | 测试连续粘贴 25 张图片 | 只保留前 20 张，提示超出限制 |

### 测试用例 6：配置引导弹窗

| 步骤 | 操作 | 预期结果 |
|------|------|---------|
| 6.1 | 确保 LVM 和 LLM 都配置完整，刷新页面 | 不弹出配置引导弹窗 |
| 6.2 | 停用 LVM 配置，刷新页面 | 弹窗显示「模型配置」下「视觉模型」为黄色/红色状态 |
| 6.3 | 点击弹窗「去配置」按钮 | 跳转到 AIModelConfig 页面 |
| 6.4 | 点击「稍后配置」 | 弹窗关闭，可正常使用文本生成功能 |

### QA 准入/准出标准

**准入条件**：
- 所有后端代码已提交，数据库迁移已执行
- 前端代码已构建（`npm run build` 通过）
- LVM 配置页面可访问

**准出条件**：
- TC1（配置连接）: ✅ 通过 — 测试连接成功
- TC2（提示词配置）: ✅ 通过 — 提示词保存并启用
- TC3（图片粘贴展示）: ✅ 通过 — 图片粘贴上传并预览渲染正常
- TC4（全链路）: ✅ 通过 — LVM 正确识别图片内容，LLM 生成的用例与图片内容相关
- TC5（异常降级）: ✅ 通过 — 降级路径不影响现有功能
- TC6（引导弹窗）: ✅ 通过 — LVM 状态正确显示和跳转
