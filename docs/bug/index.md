# Bug 跟踪汇总

> QA 会话日期：2026-06-29 ~ 2026-06-30
> 测试范围：AI 导入功能 + LVM 视觉模型支持

---

## AI Import 功能 Bug

| ID | 标题 | 优先级 | 模块 | 状态 | 修复人 | 验证人 |
|----|------|--------|------|------|--------|--------|
| [BUG-001](BUG-001.md) | 步骤 3 问题列表 string/select 类型无法回答 | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-002](BUG-002.md) | Select 下拉框选项渲染为 `[object Object]` | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-003](BUG-003.md) | 环境变量映射格式不匹配致 answers 提交失败 | P0 | 前后端契约 | 已验证 | Claude | Claude |
| [BUG-004](BUG-004.md) | 上传结果页格式始终显示 JSON、端点数始终为 0 | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-005](BUG-005.md) | 环境变量映射表格始终为空 | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-006](BUG-006.md) | 保存结果页始终显示"已创建 0 个请求" | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-007](BUG-007.md) | 多参数表格端点路径列为空 | P0 | 前端 | 已验证 | Claude | Claude |
| [BUG-008](BUG-008.md) | SSE 进度流无实际作用 + 阻塞 WSGI 工作线程 | P1 | 后端 | 暂不修复 | — | — |
| [BUG-009](BUG-009.md) | 上传接口无文件大小限制 — 内存耗尽风险 | P1 | 后端 | 已验证 | Claude | Claude |
| [BUG-010](BUG-010.md) | 前端接受 YAML 但后端仅支持 JSON | P1 | 后端 | 已验证 | Claude | Claude |
| [BUG-011](BUG-011.md) | answers/save 端点 select_for_update 无事务包裹 | P2 | 后端 | 已验证 | Claude | Claude |
| [BUG-012](BUG-012.md) | 认证方式检测扫描所有回答值匹配关键词 | P2 | 后端 | 已验证 | Claude | Claude |
| [BUG-201](BUG-201.md) | Security 认证头未被分类为 manual，缺少认证问题 | P2 | 后端 | 待修复 | — | — |
| [BUG-202](BUG-202.md) | Base URL（url_domain）答案未应用到生成请求的 URL | P2 | 后端 | 待修复 | — | — |
| [BUG-203](BUG-203.md) | Upload 进度回退（70 → 40） | P3 | 后端 | 待修复 | — | — |
| [BUG-204](BUG-204.md) | generated_summary 缺少 request_count 字段 | P3 | 后端 | 待修复 | — | — |
| [BUG-205](BUG-205.md) | SSE 完成处理无防重复调用保护 | P2 | 前端 | 待修复 | — | — |
| [BUG-206](BUG-206.md) | Fallback setTimeout 未在卸载/重置时清理 | P2 | 前端 | 待修复 | — | — |
| [BUG-207](BUG-207.md) | SSE onprogress 重复解析全部历史消息 | P2 | 前端 | 待修复 | — | — |
| [BUG-208](BUG-208.md) | handleGenerate 无问题自动跳转时 Step 卡住 | P3 | 前端 | 待修复 | — | — |
| [BUG-209](BUG-209.md) | Step 4 无重试/跳过按钮（SSE 失败时无法恢复） | P3 | 前端 | 待修复 | — | — |
| [BUG-210](BUG-210.md) | SSE 解析忽略 event: 字段，无法区分事件类型 | P3 | 前端 | 待修复 | — | — |
| [BUG-211](BUG-211.md) | 文件解析依赖扩展名而非实际内容 | P2 | 后端 | 待修复 | — | — |

## LVM 视觉模型支持 Bug

| ID | 标题 | 优先级 | 模块 | 状态 | 修复人 | 验证人 |
|----|------|--------|------|------|--------|--------|
| [BUG-101](BUG-101.md) | PromptConfig 类型徽章错误标记 data_generator / field_classify | P2 | 前端 | 已验证 | Claude | Claude |
| [BUG-102](BUG-102.md) | formatMarkdown 图片 URL 中 & 被 HTML 转义破坏 | P2 | 前端 | 已验证 | Claude | Claude |
| [BUG-103](BUG-103.md) | visonPrompt i18n key 拼写错误（死代码） | P3 | 前端 | 已验证 | Claude | Claude |
| [BUG-104](BUG-104.md) | RequirementImage 未注册 Django Admin | P3 | 后端 | 已验证 | Claude | Claude |
| [BUG-105](BUG-105.md) | LVM 功能无自动化测试覆盖 | P3 | 测试 | 暂不修复 | — | — |
| [BUG-106](BUG-106.md) | preprocess_images 中 asyncio.run 套 ThreadPoolExecutor 性能问题 | P3 | 后端 | 已验证 | Claude | Claude |

---

## 状态说明

| 状态 | 含义 |
|------|------|
| 待修复 | 已确认 Bug，尚未开始修复 |
| 修复中 | 正在修复 |
| 已修复 | 已提交修复 |
| 暂不修复 | 评估后决定推迟或不需要修复 |
| 待验证 | 需 QA 验证 |
| 已验证 | QA 验证通过 |
| 已关闭 | 验证通过，关闭 |

## 暂不修复说明

- **BUG-008**：SSE 进度流需要将生成任务改为 Celery 异步执行，涉及架构变更，当前阶段优先修复数据契约和功能 Bug。
- **BUG-105**：自动化测试需 mock API 调用，涉及测试框架搭建，作为独立任务后续规划。

## 优先级定义

| 级别 | 定义 |
|------|------|
| P0 | 阻塞核心功能，必须立即修复 |
| P1 | 严重影响使用体验或存在安全风险 |
| P2 | 功能异常但有替代路径 |
| P3 | 小问题或代码质量建议 |
