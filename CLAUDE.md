# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TestHub is an AI-driven test management platform built with Django 4.2 (backend) + Vue 3 (frontend). It provides test case management, API testing, UI automation testing, AI-powered requirement analysis, and test case generation capabilities.

## Common Commands
# Activate the virtual environment(Windows)
# d:\testhub_platform\venv\Scripts\Activate.ps1
C:\Users\Administrator\Downloads\Python-3.12.11\Python-3.12.11\Lib\venv\scripts\common\Activate.ps1
# Activate the virtual environment(MacOS)
source .venv/bin/activate

### Backend (Django)

```bash
# Start development server
python manage.py runserver

# Database migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run all scheduled tasks (API testing + UI automation)
python manage.py run_all_scheduled_tasks

# Initialize UI automation locator strategies
python manage.py init_locator_strategies

# Download webdrivers for UI automation
python manage.py download_webdrivers
```

### Frontend (Vue 3 + Vite)

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Lint code
npm run lint
```

## Architecture

### Backend Structure (`apps/`)

The Django project uses a modular app structure under `apps/`:

- **users**: User authentication and profile management (custom User model)
- **projects**: Project and team management
- **testcases**: Manual test case management with steps, attachments, comments
- **testsuites**: Test suite organization
- **executions**: Test plan execution and result tracking
- **reports**: Test report generation
- **reviews**: Test case review workflow with templates and assignments
- **versions**: Version/release management
- **requirement_analysis**: AI-powered requirement document parsing (PDF/Word/TXT) and test case generation
- **assistant**: Dify AI chatbot integration
- **api_testing**: API testing module (HTTP/WebSocket, environments, scheduled tasks, Allure reports)
- **ui_automation**: UI automation with Selenium/Playwright, element management, page objects, AI intelligent mode

### Frontend Structure (`frontend/src/`)

- **views/**: Page components organized by feature module
- **api/**: API service layer
- **stores/**: Pinia state management
- **router/**: Vue Router configuration
- **components/**: Shared components
- **layout/**: Layout components

### Key Configuration Files

- `backend/settings.py`: Django settings (database, REST framework, CORS, Celery, email)
- `frontend/vite.config.js`: Vite build configuration
- `.env`: Environment variables (DB credentials, API keys, email config)

## API Structure

All API endpoints are prefixed with `/api/`:
- `/api/auth/` and `/api/users/`: User authentication
- `/api/projects/`: Project management
- `/api/testcases/`: Test case CRUD
- `/api/testsuites/`: Test suite management
- `/api/executions/`: Test execution
- `/api/reports/`: Report generation
- `/api/reviews/`: Review workflow
- `/api/versions/`: Version management
- `/api/assistant/`: AI assistant chat
- `/api/requirement-analysis/`: AI requirement analysis
- `/api/` (api_testing): API testing endpoints
- `/api/ui-automation/`: UI automation endpoints

API documentation available at `/api/docs/` (Swagger) and `/api/redoc/` (ReDoc).

## Database

MySQL 8.0+ with `utf8mb4` charset. Configuration via environment variables:
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

## AI Integration

The platform supports multiple AI providers configured in `requirement_analysis.AIModelConfig`:
- DeepSeek, Qwen (通义千问), SiliconFlow (硅基流动), OpenAI-compatible APIs
- AI roles: `testcase_writer`, `testcase_reviewer`, `browser_use_text`, `browser_use_vision`

UI automation AI mode uses `browser-use` library with LangChain for intelligent browser automation (`apps/ui_automation/ai_agent.py`).

## Testing Prompt Templates

Custom prompts for AI test case generation are defined in:
- `tester.md`: Test case writer persona and output format
- `tester_pro.md`: Test case reviewer persona

## Key Dependencies

Backend: Django REST Framework, drf-spectacular, django-filter, celery, httpx, selenium, playwright, browser-use, langchain-openai

Frontend: Vue 3, Element Plus, Pinia, Vue Router, Axios, ECharts, Monaco Editor, xlsx

## Commit 规范
- 默认不自动提交代码
- 多个相关修改应合并为一个 commit
- commit message 格式：`<type>: <简短描述>`
- 提交前必须运行 lint 和测试
## 反思记录：LVM 视觉模型支持实现偏差

### 问题概述
2026-06-24 LVM 功能实现中，多处偏离设计文档规范，导致功能不可用（粘贴中断、预览区不显示）。

### 具体偏差

1. **私自增加未规范功能** — 在 `handlePaste` 中增加了 HTML `<img>` 标签解析逻辑（`urlToBlob` 方法），设计文档只要求处理 `clipboardData.files` 中的图片文件。额外逻辑调用 `event.preventDefault()` 阻止了正常文本粘贴，导致粘贴完全不可用。

2. **预览区实现不当** — 用行内正则 `v-if` 作为条件，未使用 computed 属性。预览区 CSS 使用了未定义的 `markdown-body` class。

3. **默认提示词未对标项目** — 首次编写的 vision 默认提示词为自创的长篇风格，未参照 `D:\AI\AItest` 对标项目的简洁风格（2 句话 + 输出格式）。后虽按用户要求修正，但应在编写前先查看对标项目。

4. **语法错误** — 硬编码默认提示词中使用 `"""..."""` 三引号嵌套了中文双引号，导致 Python SyntaxError，500 错误。

5. **reguirement_images 表缺失** — 迁移被 `--fake` 应用但实际表未创建，导致 LVM 图片上传 API 返回 500。

### 根因分析

- **未严格遵循设计文档**：设计文档已经写明每个模块的具体代码、接口、行为。不应增加未规范的功能。
- **未先验证对标项目**：用户提供了 `D:\AI\AItest` 作为参考，应先查看其 prompts 目录下的现有实现再动手。
- **未做回归测试**：修改 paste 处理逻辑后未验证普通文本粘贴是否正常。
- **迁移管理不当**：使用 `--fake` 后未验证表是否真实创建。

### 改进措施

- 实现严格按设计文档，不增加未要求的字段、方法、功能
- 涉及剪贴板/事件处理时，`event.preventDefault()` 只在确需接管时调用，否则让浏览器默认行为继续
- 有对标项目时先看对标项目的实现，精确复制而非自创
- 数据库迁移用 `--fake` 后必须验证表结构存在
- 修改核心交互（粘贴、上传）后手动回归测试基本功能
