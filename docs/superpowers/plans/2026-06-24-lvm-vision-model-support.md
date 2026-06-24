# LVM 视觉模型支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LVM (Large Vision Model) support to TestHub's AI requirement analysis to enable processing of images (flowcharts, UI screenshots) pasted into requirement descriptions.

**Architecture:** Two-stage pipeline: Stage 1 — LVM preprocessor scans requirement text for Markdown image references (`![alt](url)`), downloads images, calls a vision model (OpenAI-compatible multimodal API) to get text descriptions, and replaces references with descriptions. Stage 2 — processed text is fed unchanged into the existing LLM writer/reviewer pipeline. LVM config added as a new `vision` role in the existing `AIModelConfig` model.

**Tech Stack:** Django 4.2 + Django REST Framework, Vue 3 + Element Plus, asyncio + ThreadPoolExecutor for concurrent LVM calls, SSE for streaming

**Design Doc:** `docs/superpowers/specs/2026-06-24-lvm-vision-model-support-design.md`

## Global Constraints

- LVM is optional enhancement — existing LLM-only flow must remain unchanged
- All new API endpoints under `/api/requirement-analysis/` prefix
- Image upload max 10MB per file, max 20 images per paste session
- Supported image formats: png, jpg/jpeg, gif, webp
- LVM uses OpenAI-compatible multimodal format (same `call_openai_compatible_api` interface)
- Concurrency: 3 parallel workers for LVM image processing
- All UI changes must follow existing card-based, Element Plus styling
- i18n: add both zh-cn and en locale keys

---

## File Structure

### Files to modify:
- `apps/requirement_analysis/models.py` — ROLE_CHOICES, PROMPT_CHOICES, RequirementImage model, preprocess_images method
- `apps/requirement_analysis/views.py` — UploadImageView, modify generate() for LVM step, extend config check
- `apps/requirement_analysis/urls.py` — add upload-image route
- `apps/requirement_analysis/serializers.py` — possibly RequirementImageSerializer (small)
- `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue` — paste handler, image upload, markdown image rendering, config guide extension
- `frontend/src/views/requirement-analysis/PromptConfig.vue` — add vision filter/form option
- `frontend/src/views/requirement-analysis/AIModelConfig.vue` — add vision role filter option
- `frontend/src/locales/lang/zh-cn/requirement.js` — i18n keys
- `frontend/src/locales/lang/en/requirement.js` — i18n keys
- `frontend/src/api/requirement-analysis.js` — uploadImage API method

### Files to create:
- None (all changes are additions to existing files)

---

### Task 1: Backend — Add `vision` role to AIModelConfig and PromptConfig

**Files:**
- Modify: `apps/requirement_analysis/models.py` lines ~211-215 (ROLE_CHOICES), lines ~253-256 (PROMPT_CHOICES)

**Interfaces:**
- Consumes: existing AIModelConfig model pattern
- Produces: `AIModelConfig.ROLE_CHOICES` includes `('vision', '视觉模型（LVM）')`, `PromptConfig.PROMPT_CHOICES` includes `('vision', '图片分析提示词')`

- [ ] **Step 1: Add `vision` to ROLE_CHOICES**

In `apps/requirement_analysis/models.py`, find ROLE_CHOICES (around line 211):
```python
ROLE_CHOICES = [
    ('writer', '测试用例编写专家'),
    ('reviewer', '测试评审专家'),
    ('browser_use_text', 'Browser Use - 文本模式'),
]
```
Add `('vision', '视觉模型（LVM）'),` after `browser_use_text`:
```python
ROLE_CHOICES = [
    ('writer', '测试用例编写专家'),
    ('reviewer', '测试评审专家'),
    ('browser_use_text', 'Browser Use - 文本模式'),
    ('vision', '视觉模型（LVM）'),
]
```

- [ ] **Step 2: Add `vision` to PROMPT_CHOICES**

Find PROMPT_CHOICES (around line 253):
```python
PROMPT_CHOICES = [
    ('writer', '用例编写提示词'),
    ('reviewer', '用例评审提示词'),
]
```
Add `('vision', '图片分析提示词'),`:
```python
PROMPT_CHOICES = [
    ('writer', '用例编写提示词'),
    ('reviewer', '用例评审提示词'),
    ('vision', '图片分析提示词'),
]
```

- [ ] **Step 3: Create database migration**

Run:
```bash
python manage.py makemigrations requirement_analysis
python manage.py migrate requirement_analysis
```
Expected: migration created for altered choices (no schema change since choices are app-level only).

- [ ] **Step 4: Commit**

```bash
git add apps/requirement_analysis/models.py
git commit -m "feat: add vision role to AIModelConfig and PromptConfig"
```

---

### Task 2: Backend — RequirementImage model and upload API

**Files:**
- Create `apps/requirement_analysis/models.py` — RequirementImage model (append before AIModelService class)
- Modify `apps/requirement_analysis/views.py` — UploadImageView
- Modify `apps/requirement_analysis/urls.py` — add route
- Modify `apps/requirement_analysis/serializers.py` — RequirementImageSerializer

**Interfaces:**
- Consumes: Django User model, ImageField
- Produces: `POST /api/requirement-analysis/upload-image/` returns `{"id": 1, "url": "/media/...", "filename": "image.png"}`

- [ ] **Step 1: Add RequirementImage model**

In `apps/requirement_analysis/models.py`, add before the `AIModelService` class (before line ~421):
```python
class RequirementImage(models.Model):
    """需求分析上传的图片模型"""
    file = models.ImageField(upload_to='requirement_images/%Y/%m/', verbose_name='图片文件')
    filename = models.CharField(max_length=255, verbose_name='原始文件名')
    description = models.TextField(blank=True, verbose_name='LVM识别描述')
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='上传者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'requirement_images'
        verbose_name = '需求图片'
        verbose_name_plural = '需求图片'

    def __str__(self):
        return self.filename
```

Also add `RequirementImage` to the imports in views.py — it's already imported via `from .models import (...)` but need to add it to the import list (line 34-38). Add `RequirementImage` to the existing import.

- [ ] **Step 2: Add RequirementImageSerializer**

In `apps/requirement_analysis/serializers.py`, add after existing serializers (before `TestCaseGenerationRequestSerializer`):
```python
class RequirementImageSerializer(serializers.ModelSerializer):
    """需求图片序列化器"""
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = RequirementImage
        fields = ['id', 'file', 'file_url', 'filename', 'description', 'created_at']
        read_only_fields = ['id', 'file_url', 'filename', 'description', 'created_at']

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None
```

- [ ] **Step 3: Add UploadImageView**

In `apps/requirement_analysis/views.py`, add after imports and before viewset classes (after line ~50):

```python
class UploadImageView(APIView):
    """图片上传视图"""
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': '请选择要上传的图片'}, status=status.HTTP_400_BAD_REQUEST)

        # 验证文件类型
        import imghdr
        # 使用文件扩展名判断
        ext = os.path.splitext(file.name)[1].lower()
        allowed_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp']
        if ext not in allowed_extensions:
            return Response({'error': f'不支持的图片格式: {ext}，支持: png/jpg/gif/webp'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 验证文件大小 (10MB)
        if file.size > 10 * 1024 * 1024:
            return Response({'error': '图片大小不能超过10MB'}, status=status.HTTP_400_BAD_REQUEST)

        # 创建记录
        image = RequirementImage.objects.create(
            file=file,
            filename=file.name,
            uploaded_by=request.user
        )

        serializer = RequirementImageSerializer(image, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)
```

Also add imports at the top of views.py:
```python
from rest_framework.views import APIView
```
And add `RequirementImage` to the model imports (line 34-38):
```python
from .models import (
    RequirementDocument, RequirementAnalysis, BusinessRequirement,
    GeneratedTestCase, AnalysisTask, AIModelConfig, PromptConfig, TestCaseGenerationTask,
    GenerationConfig, AIModelService, RequirementImage
)
```
And add `RequirementImageSerializer` to serializer imports (line 39-46):
```python
from .serializers import (
    RequirementDocumentSerializer, RequirementAnalysisSerializer,
    BusinessRequirementSerializer, GeneratedTestCaseSerializer,
    AnalysisTaskSerializer, DocumentUploadSerializer,
    TestCaseGenerationRequestSerializer, TestCaseReviewRequestSerializer,
    AIModelConfigSerializer, PromptConfigSerializer, TestCaseGenerationTaskSerializer,
    GenerationConfigSerializer, RequirementImageSerializer
)
```

- [ ] **Step 4: Add route for upload-image**

In `apps/requirement_analysis/urls.py`, add `UploadImageView` to imports and add path:
```python
from .views import (
    RequirementDocumentViewSet, RequirementAnalysisViewSet,
    BusinessRequirementViewSet, GeneratedTestCaseViewSet,
    AnalysisTaskViewSet, AIModelConfigViewSet, PromptConfigViewSet,
    GenerationConfigViewSet, TestCaseGenerationTaskViewSet,
    ConfigStatusViewSet, upload_and_analyze, analyze_text,
    UploadImageView
)

urlpatterns = [
    path('', include(router.urls)),
    path('upload-image/', UploadImageView.as_view(), name='upload-image'),
    path('upload-and-analyze/', upload_and_analyze, name='upload-and-analyze'),
    path('analyze-text/', analyze_text, name='analyze-text'),
]
```

- [ ] **Step 5: Create migration and test**

```bash
python manage.py makemigrations requirement_analysis
python manage.py migrate requirement_analysis
```
Expected: new `requirement_images` table created.

Test manually with curl:
```bash
curl -X POST http://localhost:8000/api/requirement-analysis/upload-image/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test.png"
```
Expected: returns JSON with id, file_url, filename.

- [ ] **Step 6: Commit**

```bash
git add apps/requirement_analysis/models.py apps/requirement_analysis/views.py apps/requirement_analysis/urls.py apps/requirement_analysis/serializers.py
git commit -m "feat: add RequirementImage model and upload API"
```

---

### Task 3: Backend — LVM preprocess_images method in AIModelService

**Files:**
- Modify: `apps/requirement_analysis/models.py` — add `preprocess_images` static method to AIModelService class

**Interfaces:**
- Consumes: `requirement_text: str`, `lvm_config: AIModelConfig`, `vision_prompt: str`
- Produces: `processed_text: str` with `![alt](url)` replaced by LVM-generated text descriptions

- [ ] **Step 1: Add import for concurrent execution**

In `apps/requirement_analysis/models.py`, add to existing imports (top of file):
```python
import base64
from concurrent.futures import ThreadPoolExecutor, asyncio
import tempfile
```

- [ ] **Step 2: Add `preprocess_images` method to AIModelService**

Add this method to the AIModelService class (after `renumber_test_cases`, before the class ends — before line ~1279):

```python
@staticmethod
async def preprocess_images(requirement_text: str, lvm_config: 'AIModelConfig', vision_prompt: str) -> str:
    """
    扫描需求文本中的图片标记，并发调用 LVM 识别并替换为文字描述。

    Args:
        requirement_text: 包含 ![alt](url) 标记的需求文本
        lvm_config: 视觉模型配置（role='vision' 的 AIModelConfig）
        vision_prompt: 图片分析提示词

    Returns:
        处理后的文本，图片标记被替换为 LVM 描述文本
    """
    import re
    if not requirement_text:
        return requirement_text

    # 匹配 Markdown 图片语法: ![alt](url)
    image_pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')
    matches = list(image_pattern.finditer(requirement_text))

    if not matches:
        return requirement_text

    logger.info(f"发现 {len(matches)} 张图片需要 LVM 分析")

    # 用于存储替换结果
    replacements = {}
    cache = {}  # URL -> description 缓存

    def process_single_image(match):
        """处理单张图片：下载 -> base64 -> LVM调用 -> 获取描述"""
        alt_text = match.group(1)
        url = match.group(2)

        # 缓存命中
        if url in cache:
            return cache[url]

        try:
            # 下载图片
            import httpx
            if url.startswith('/media/'):
                # 本地 media 文件
                from django.conf import settings
                local_path = str(settings.BASE_DIR) + url
                if os.path.exists(local_path):
                    with open(local_path, 'rb') as f:
                        image_data = f.read()
                else:
                    # 尝试 MEDIA_ROOT
                    media_path = str(settings.MEDIA_ROOT) + url.replace('/media', '')
                    if os.path.exists(media_path):
                        with open(media_path, 'rb') as f:
                            image_data = f.read()
                    else:
                        logger.warning(f"图片文件不存在: {url}")
                        return None
            else:
                # 远程 URL
                response = httpx.get(url, timeout=30)
                response.raise_for_status()
                image_data = response.content

            # 转为 base64
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            # 获取图片格式
            ext = os.path.splitext(url.split('?')[0])[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                       '.gif': 'image/gif', '.webp': 'image/webp'}
            mime_type = mime_map.get(ext, 'image/png')

            # 构建 multimodal 消息
            messages = [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                {"type": "text", "text": vision_prompt}
            ]}]

            # 调用 LVM
            response = AIModelService.call_openai_compatible_api(lvm_config, messages)
            description = response['choices'][0]['message']['content'].strip()

            cache[url] = description
            logger.info(f"LVM 分析图片成功: {alt_text or url[:50]} -> {description[:50]}...")
            return description

        except Exception as e:
            logger.error(f"LVM 分析图片失败 [{url[:50]}]: {e}")
            return None

    # 并发处理所有图片
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for match in matches:
            futures.append(loop.run_in_executor(executor, process_single_image, match))

        results = await asyncio.gather(*futures, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"图片处理异常: {result}")
                continue
            if result:
                replacements[matches[i].group(0)] = f"> {result}"

    # 执行替换
    processed_text = requirement_text
    for original, replacement in replacements.items():
        processed_text = processed_text.replace(original, replacement)

    unchanged = len(matches) - len(replacements)
    if unchanged > 0:
        logger.info(f"LVM 预处理完成: 成功 {len(replacements)} 张, 失败 {unchanged} 张")
    else:
        logger.info(f"LVM 预处理完成: 全部 {len(replacements)} 张图片处理成功")

    return processed_text
```

- [ ] **Step 3: Commit**

```bash
git add apps/requirement_analysis/models.py
git commit -m "feat: add preprocess_images method for LVM image analysis"
```

---

### Task 4: Backend — Integrate LVM preprocessing into generation pipeline

**Files:**
- Modify: `apps/requirement_analysis/views.py` — `TestCaseGenerationTaskViewSet.generate` method

**Interfaces:**
- Consumes: `TestCaseGenerationTask` with `requirement_text` containing `![alt](url)`, active `vision` AIModelConfig and `vision` PromptConfig
- Produces: `task.requirement_text` modified with image references replaced by LVM descriptions

- [ ] **Step 1: Add LVM preprocessing step in execute_task**

In `views.py`, in the `execute_task()` function inside `generate()`, find the section where `task.progress = 10` and `task.save()` (around line 1561-1562). Add LVM preprocessing right after:

After the block:
```python
task.status = 'generating'
task.progress = 10
task.save()
```

Add LVM preprocessing:
```python
# === LVM 预处理：扫描需求文本中的图片并调用视觉模型分析 ===
try:
    lvm_config = AIModelConfig.objects.filter(role='vision', is_active=True).first()
    vision_prompt = PromptConfig.get_active_config('vision')
    if lvm_config and vision_prompt:
        logger.info(f"任务 {task.task_id}: 检测到 LVM 配置，开始图片预处理")
        # 更新进度
        task.status = 'generating'
        task.progress = 15
        task.save()

        processed_text = loop.run_until_complete(
            AIModelService.preprocess_images(task.requirement_text, lvm_config, vision_prompt.content)
        )

        if processed_text != task.requirement_text:
            task.requirement_text = processed_text
            task.save(update_fields=['requirement_text'])
            logger.info(f"任务 {task.task_id}: LVM 预处理完成，已更新需求文本")
except Exception as e:
    logger.warning(f"任务 {task.task_id}: LVM 预处理失败，降级为纯文本处理: {e}")
    # 不阻断流程，继续使用原文
```

This code should be placed just before the check `if task.output_mode == 'stream':` (around line 1580). It uses the existing `loop` variable that's already created, and is in the same task context.

- [ ] **Step 2: Verify the flow**

Read the code at lines 1575-1585 to confirm where `loop` is created:
```python
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

try:
    # 根据输出模式选择不同的生成方式
    if task.output_mode == 'stream':
```

The LVM preprocessing should go between `asyncio.set_event_loop(loop)` and the `if task.output_mode == 'stream':` check.

- [ ] **Step 3: Commit**

```bash
git add apps/requirement_analysis/views.py
git commit -m "feat: integrate LVM preprocessing into generation pipeline"
```

---

### Task 5: Backend — Extend config check with LVM status

**Files:**
- Modify: `apps/requirement_analysis/views.py` — `ConfigStatusViewSet.check`

**Interfaces:**
- Produces: extended JSON response with `lvm_model` and `vision_prompt` fields

- [ ] **Step 1: Add LVM config check logic**

In `ConfigStatusViewSet.check()`, after the reviewer prompt checks (around line 3229), add:

```python
# 检查LVM模型配置
lvm_model_enabled = AIModelConfig.objects.filter(
    role='vision', is_active=True
).first()

lvm_model_disabled = AIModelConfig.objects.filter(
    role='vision', is_active=False
).first()

# 检查视觉分析提示词
vision_prompt_enabled = PromptConfig.objects.filter(
    prompt_type='vision', is_active=True
).first()

vision_prompt_disabled = PromptConfig.objects.filter(
    prompt_type='vision', is_active=False
).first()
```

- [ ] **Step 2: Add LVM fields to response_data**

In the `response_data` dict (around line 3272), add after `generation_config`:

```python
'lvm_model': {
    'configured': lvm_model_enabled is not None or lvm_model_disabled is not None,
    'enabled': lvm_model_enabled is not None,
    'name': (lvm_model_enabled or lvm_model_disabled).name if (
            lvm_model_enabled or lvm_model_disabled) else None,
    'id': (lvm_model_enabled or lvm_model_disabled).id if (
            lvm_model_enabled or lvm_model_disabled) else None,
    'required': False
},
'vision_prompt': {
    'configured': vision_prompt_enabled is not None or vision_prompt_disabled is not None,
    'enabled': vision_prompt_enabled is not None,
    'name': (vision_prompt_enabled or vision_prompt_disabled).name if (
            vision_prompt_enabled or vision_prompt_disabled) else None,
    'id': (vision_prompt_enabled or vision_prompt_disabled).id if (
            vision_prompt_enabled or vision_prompt_disabled) else None,
    'required': False
},
```

- [ ] **Step 3: Commit**

```bash
git add apps/requirement_analysis/views.py
git commit -m "feat: add LVM config status to config check endpoint"
```

---

### Task 6: Frontend — Add paste and image upload to RequirementAnalysisView

**Files:**
- Modify: `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue`
- Modify: `frontend/src/api/requirement-analysis.js`

- [ ] **Step 1: Add uploadImage API method**

In `frontend/src/api/requirement-analysis.js`, add at the end:
```javascript
// ==================== 图片上传 ====================

// 上传需求图片
export function uploadImage(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/requirement-analysis/upload-image/',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
```

- [ ] **Step 2: Add paste handler data properties**

In `RequirementAnalysisView.vue`, add to `data()` (around line 350):
```javascript
// 图片上传相关
uploadingImages: false,
pastedImageCount: 0,
MAX_IMAGES: 20,
```

- [ ] **Step 3: Add handlePaste method**

In `methods`, add:
```javascript
async handlePaste(event) {
  const items = event.clipboardData?.items
  if (!items) return

  for (let i = 0; i < items.length; i++) {
    const item = items[i]
    if (item.type.startsWith('image/')) {
      event.preventDefault() // 阻止默认粘贴行为

      if (this.pastedImageCount >= this.MAX_IMAGES) {
        ElMessage.warning(`最多支持 ${this.MAX_IMAGES} 张图片`)
        return
      }

      const file = item.getAsFile()
      if (file.size > 10 * 1024 * 1024) {
        ElMessage.warning('单张图片不能超过10MB')
        return
      }

      this.uploadingImages = true
      try {
        const response = await uploadImage(file)
        const imageUrl = response.data.file_url || response.data.url
        const imageMarkdown = `![${file.name}](${imageUrl})`

        // 在光标位置插入
        const textarea = this.$el.querySelector('.form-textarea')
        const cursorPos = textarea.selectionStart
        const textBefore = this.manualInput.description.substring(0, cursorPos)
        const textAfter = this.manualInput.description.substring(cursorPos)
        this.manualInput.description = textBefore + imageMarkdown + textAfter

        // 更新光标位置
        this.$nextTick(() => {
          textarea.selectionStart = cursorPos + imageMarkdown.length
          textarea.selectionEnd = cursorPos + imageMarkdown.length
          textarea.focus()
        })

        this.pastedImageCount++
      } catch (error) {
        console.error('图片上传失败:', error)
        ElMessage.error('图片上传失败')
      } finally {
        this.uploadingImages = false
      }
    }
  }
},
```

- [ ] **Step 4: Update formatMarkdown to render images**

Find the `formatMarkdown` method (around line 1211). Add image rendering at the beginning, after the content cleaning step:

After `html = content.replace(/新增-/g, '');` (around line 1218):

```javascript
// 渲染图片 ![alt](url) -> <img>
html = html.replace(/!\[(.*?)\]\((.*?)\)/g, '<div class="image-container"><img src="$2" alt="$1" class="preview-image" onclick="window.open(\'$2\',\'_blank\')"></div>');
```

- [ ] **Step 5: Add paste event binding to textarea**

Find the textarea element (around line 133-138):
```html
<textarea
  v-model="manualInput.description"
  class="form-textarea"
  rows="8"
  :placeholder="$t('requirementAnalysis.descriptionPlaceholder')">
</textarea>
```

Add `@paste="handlePaste"`:
```html
<textarea
  v-model="manualInput.description"
  class="form-textarea"
  rows="8"
  :placeholder="$t('requirementAnalysis.descriptionPlaceholder')"
  @paste="handlePaste">
</textarea>
```

- [ ] **Step 6: Add image preview CSS**

In the `<style scoped>` section, add:
```css
.image-container {
  margin: 10px 0;
  text-align: center;
}

.preview-image {
  max-width: 100%;
  max-height: 500px;
  border: 1px solid #e1e8ed;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  cursor: zoom-in;
  transition: transform 0.2s ease;
}

.preview-image:hover {
  transform: scale(1.02);
}
```

- [ ] **Step 7: Reset image count on new generation**

In `resetGeneration()` method (around line 1175), add:
```javascript
this.pastedImageCount = 0;
```

In `startGeneration()` method (around line 722), add at the beginning:
```javascript
this.pastedImageCount = 0;
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/requirement-analysis.js frontend/src/views/requirement-analysis/RequirementAnalysisView.vue
git commit -m "feat: add image paste and upload support to requirement analysis page"
```

---

### Task 7: Frontend — Update config guide modal with LVM status

**Files:**
- Modify: `frontend/src/views/requirement-analysis/RequirementAnalysisView.vue`
- Modify: `frontend/src/locales/lang/zh-cn/requirement.js`
- Modify: `frontend/src/locales/lang/en/requirement.js`

- [ ] **Step 1: Extend configStatus data with LVM fields**

In `data()` section (around line 386), add LVM fields to `configStatus`:
```javascript
lvm_model: {
  configured: false,
  enabled: false,
  name: null,
  id: null,
  required: false
},
vision_prompt: {
  configured: false,
  enabled: false,
  name: null,
  id: null,
  required: false
},
```

- [ ] **Step 2: Add LVM rows in config guide modal**

In the template, after the reviewer_prompt config line (around line 64), add vision_prompt row:

```html
<!-- 加入提示词配置分组中 -->
<div class="config-item-inline" :class="getConfigItemClass('vision_prompt')">
  <span class="status-symbol" v-html="getStatusSymbol('vision_prompt')"></span>
  <span class="config-label">{{ $t('configGuide.imageAnalysis') }}</span>
  <span class="config-name" v-if="configStatus.vision_prompt.name">{{ configStatus.vision_prompt.name }}</span>
  <span class="status-text" v-if="!configStatus.vision_prompt.configured">{{ $t('configGuide.unconfigured') }}</span>
  <span class="status-text warning" v-else-if="!configStatus.vision_prompt.enabled">{{ $t('configGuide.disabled') }}</span>
</div>
```

After the reviewer_model config line (around line 42), add lvm_model row:
```html
<!-- 加入模型配置分组中 -->
<div class="config-item-inline" :class="getConfigItemClass('lvm_model')">
  <span class="status-symbol" v-html="getStatusSymbol('lvm_model')"></span>
  <span class="config-label">{{ $t('configGuide.visionModel') }}</span>
  <span class="config-name" v-if="configStatus.lvm_model.name">{{ configStatus.lvm_model.name }}</span>
  <span class="status-text" v-if="!configStatus.lvm_model.configured">{{ $t('configGuide.unconfigured') }}</span>
  <span class="status-text warning" v-else-if="!configStatus.lvm_model.enabled">{{ $t('configGuide.disabled') }}</span>
</div>
```

- [ ] **Step 3: Add i18n keys for zh-cn**

In `frontend/src/locales/lang/zh-cn/requirement.js`, find `configGuide` section and add:
```javascript
configGuide: {
  // 原有...
  imageAnalysis: '图片分析',
  visionModel: '视觉模型',
  visionLvm: '视觉模型(LVM)',
  visionHint: '推荐配置视觉模型以识别需求文档中的图片',
}
```

- [ ] **Step 4: Add i18n keys for en**

In `frontend/src/locales/lang/en/requirement.js`, in the `configGuide` section add:
```javascript
configGuide: {
  // existing keys remain...
  imageAnalysis: 'Image Analysis',
  visionModel: 'Vision Model',
  visionLvm: 'Vision Model (LVM)',
  visionHint: 'Configure a vision model to analyze images in requirements',
}
```

Also add to `promptConfig` section of English locale:
```javascript
promptConfig: {
  // existing keys remain...
  imageAnalyzer: 'Image Analysis',
  visonPrompt: 'Image Analysis Prompt',
}
```

Add to `aiModelConfig` section of English locale:
```javascript
aiModel: {
  // existing keys remain...
  roles: {
    // existing roles remain...
    vision: 'Vision Model (LVM)',
  },
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/requirement-analysis/RequirementAnalysisView.vue frontend/src/locales/
git commit -m "feat: add LVM status to config guide modal and i18n"
```

---

### Task 8: Frontend — Update AIModelConfig and PromptConfig role/type filters

**Files:**
- Modify: `frontend/src/views/requirement-analysis/AIModelConfig.vue`
- Modify: `frontend/src/views/requirement-analysis/PromptConfig.vue`

- [ ] **Step 1: Update AIModelConfig.vue — add vision option to role filter**

In `AIModelConfig.vue`, find the role filter dropdown (role selector for filtering list and form). Add the vision option. Look for existing role badge display (around line 31):
```html
<span class="role-badge" :class="config.role">
  {{ $t('configuration.aiModel.roles.' + config.role) }}
</span>
```

Check if there's a `zh-cn/configuration.js` where roles are defined. If so, add `vision: '视觉模型(LVM)'` to the roles object.

- [ ] **Step 2: Update PromptConfig.vue — add vision option to type filter**

In `PromptConfig.vue`, find the type-badge display (around line 29-31):
```html
<span class="type-badge" :class="config.prompt_type">
  {{ config.prompt_type === 'writer' ? $t('promptConfig.writerPrompt') : $t('promptConfig.reviewerPrompt') }}
</span>
```
Update to handle three types:
```html
<span class="type-badge" :class="config.prompt_type">
  {{ config.prompt_type === 'writer' ? $t('promptConfig.writerPrompt') : (config.prompt_type === 'reviewer' ? $t('promptConfig.reviewerPrompt') : $t('promptConfig.imageAnalyzer')) }}
</span>
```

Find the prompt_type selector in the add/edit form (around line ~350 area in PromptConfig.vue), add the third option:
```html
<option value="vision">{{ $t('promptConfig.imageAnalyzer') }}</option>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/requirement-analysis/AIModelConfig.vue frontend/src/views/requirement-analysis/PromptConfig.vue
git commit -m "feat: add vision option to AIModelConfig and PromptConfig UI"
```

---

### Task 9: QA — Manual verification of complete LVM pipeline

**Files:**
- Verify: full-stack manual testing

- [ ] **Step 1: Run migrations and start servers**

```bash
python manage.py migrate
python manage.py runserver  # in one terminal

cd frontend && npm run dev   # in another terminal
```

- [ ] **Step 2: Execute QA Test Cases from design doc**

Follow the QA section in the design doc (`docs/superpowers/specs/2026-06-24-lvm-vision-model-support-design.md` lines 238-331), complete all 6 test cases:

1. **TC1**: LVM configuration — create AIModelConfig with role=vision, test connection with credentials provided
2. **TC2**: Vision prompt config — create PromptConfig with type=vision
3. **TC3**: Image paste and display — paste screenshot, verify inline rendering
4. **TC4**: Full pipeline — paste image with PRD text, generate, verify LVM+LLM output
5. **TC5**: Degradation — disable LVM, verify existing LLM flow works
6. **TC6**: Config guide modal — verify LVM status display

- [ ] **Step 3: Record results**

Document which test cases pass/fail. If any fail, fix and re-test.
