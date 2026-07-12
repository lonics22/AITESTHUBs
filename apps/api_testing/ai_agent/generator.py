"""AnalyzeGenerator — 分析端点并通过 LLM 生成带 _mode 标记的测试用例模板"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from django.conf import settings

from apps.api_testing.models import AIImportTask
from apps.api_testing.ai_agent.prompts import TEST_CASE_GENERATION_PROMPT
from apps.api_testing.ai_agent.schema import validate_generator_output
from apps.api_testing.ai_agent.mode_strategy import apply_user_preference

logger = logging.getLogger(__name__)

# 每批最大端点数量
BATCH_SIZE = 20
# 最大重试次数
MAX_RETRIES = 2


class AnalyzeGenerator:
    """分析端点 -> LLM 生成带标记的测试用例模板。

    使用流程：
    1. 加载 AIImportTask，获取已解析的端点列表
    2. 构建提示词（注入 tester.md 风格 + 端点定义）
    3. 调用 LLM 一次性生成所有测试用例
    4. 校验并通过 validate_generator_output()
    5. 校验失败时自动重试（最多 2 次）
    6. 端点数量 > 20 时分批次生成

    Phase 2 新增：事件系统（on/_emit），支持 SSE 流式进度推送。
    """

    def __init__(self, task_id: int):
        """初始化生成器。

        Args:
            task_id: AIImportTask 的主键 ID
        """
        self.task_id = task_id
        self._task: Optional[AIImportTask] = None
        self._tester_md_content: Optional[str] = None
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 事件系统（Phase 2 SSE 支持）
    # ------------------------------------------------------------------

    def on(self, event: str, handler: Callable):
        """注册事件处理器"""
        self._handlers[event].append(handler)

    def _emit(self, event: str, data: dict):
        """发射事件到所有注册的处理器"""
        for handler in self._handlers.get(event, []):
            try:
                handler(data)
            except Exception as e:
                logger.warning("Generator 事件处理器异常 (%s): %s", event, e)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def generate(self) -> List[dict]:
        """加载任务 -> 获取端点 -> 调 LLM -> 校验 -> 返回。

        Returns:
            校验通过的带 _mode 标记的测试用例列表

        Raises:
            RuntimeError: 任务加载失败、LLM 调用失败、重试耗尽
        """
        task = self._get_task()
        if not task.parsed_endpoints:
            raise RuntimeError("任务没有已解析的端点，请先上传并解析文档")

        endpoints: List[dict] = task.parsed_endpoints
        total = len(endpoints)
        all_cases: List[dict] = []

        if total > BATCH_SIZE:
            logger.info("端点数量 %d > %d，分批次生成", total, BATCH_SIZE)
            for i in range(0, total, BATCH_SIZE):
                batch = endpoints[i: i + BATCH_SIZE]
                batch_cases = self._generate_batch(batch, i // BATCH_SIZE + 1)
                all_cases.extend(batch_cases)
                logger.info(
                    "批次 %d/%d 完成: 生成了 %d 条用例",
                    i // BATCH_SIZE + 1,
                    (total + BATCH_SIZE - 1) // BATCH_SIZE,
                    len(batch_cases),
                )
        else:
            all_cases = self._generate_batch(endpoints, 1)

        logger.info(
            "AnalyzeGenerator.generate: 共生成 %d 条测试用例", len(all_cases)
        )
        return all_cases

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_task(self) -> AIImportTask:
        """加载并缓存 AIImportTask 实例。"""
        if self._task is None:
            try:
                self._task = AIImportTask.objects.get(id=self.task_id)
            except AIImportTask.DoesNotExist:
                raise RuntimeError(f"任务 {self.task_id} 不存在")
        return self._task

    def _build_preference_hints(self, endpoints: List[dict]) -> str:
        """查询用户对字段的历史偏好，生成为 prompt 提示。

        Phase 3 记忆系统：如果用户历史上倾向于把某些字段标记为 user_input，
        在 prompt 中提示 LLM 优先使用该模式，减少用户后编辑工作量。

        Args:
            endpoints: 当前待分析的端点列表

        Returns:
            偏好提示字符串，没有偏好时返回空字符串
        """
        task = self._get_task()
        user_id = getattr(task.created_by, 'id', None)
        if not user_id:
            return ''

        from apps.api_testing.ai_agent.memory import get_user_preference

        hints: List[str] = []
        seen_fields: set = set()

        for ep in endpoints:
            ep_path = ep.get('path', '')

            # 从 parameters 中收集字段
            for param in ep.get('parameters', []):
                field_name = param.get('name', '')
                if not field_name or field_name in seen_fields:
                    continue
                seen_fields.add(field_name)

                mode = get_user_preference(user_id, field_name, ep_path)
                if mode:
                    hints.append(
                        f"- 字段 `{field_name}`（{ep.get('method', '')} {ep_path}）"
                        f"：请使用 `{mode}` 模式（基于历史偏好）"
                    )

            # 从 request_body 中收集字段
            body = ep.get('request_body') or ep.get('requestBody') or {}
            if isinstance(body, dict):
                # 尝试提取 schema 中的 properties
                schema = body.get('schema', body)
                properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
                for field_name in properties:
                    if field_name in seen_fields:
                        continue
                    seen_fields.add(field_name)
                    mode = get_user_preference(user_id, field_name, ep_path)
                    if mode:
                        hints.append(
                            f"- 字段 `{field_name}`（{ep.get('method', '')} {ep_path} 请求体）"
                            f"：请使用 `{mode}` 模式（基于历史偏好）"
                        )

        if not hints:
            return ''

        return (
            "### 用户偏好提醒（基于历史记录）:\n"
            "以下字段根据历史使用记录，推荐指定 _mode 模式：\n"
            + '\n'.join(hints)
        )

    def _get_tester_md(self) -> str:
        """读取 docs/tester.md 文件内容作为 prompt 模板的一部分。

        Returns:
            tester.md 的内容字符串，文件不存在或读取失败时返回空字符串
        """
        if self._tester_md_content is not None:
            return self._tester_md_content

        # 尝试多个可能的路径
        possible_paths = [
            os.path.join(settings.BASE_DIR, "docs", "tester.md"),
            os.path.join(settings.BASE_DIR, "..", "docs", "tester.md"),
            os.path.join(settings.BASE_DIR, "../../docs/tester.md"),
        ]

        for md_path in possible_paths:
            full_path = os.path.abspath(md_path)
            if os.path.isfile(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._tester_md_content = content
                    logger.info("已加载 tester.md: %s", full_path)
                    return content
                except (IOError, OSError) as e:
                    logger.warning("读取 tester.md 失败 (%s): %s", full_path, e)

        logger.warning("tester.md 未找到，将使用内置提示词")
        self._tester_md_content = ""
        return ""

    def _get_prompt_template(self) -> str:
        """获取 prompt 模板，优先从 DB PromptConfig 读取，无配置时 fallback 到 prompts.py 默认值。

        Returns:
            prompt 模板字符串（含 {endpoints_json}、{extra_instructions} 占位符）
        """
        from apps.requirement_analysis.models import PromptConfig
        config = PromptConfig.get_active_config('api_import')
        if config and config.content:
            logger.info("使用 DB PromptConfig: %s (api_import)", config.name)
            return config.content
        logger.info("未找到 api_import 的 DB prompt 配置，使用默认模板")
        return TEST_CASE_GENERATION_PROMPT

    def _build_prompt(self, endpoints: List[dict], batch_label: str = "") -> str:
        """构建 LLM 调用提示词。

        结合 tester.md 风格 + 端点定义 JSON + 用户偏好记忆（Phase 3）。

        Args:
            endpoints: 待分析的端点列表
            batch_label: 批次标签（如 "（第 1 批/共 3 批）"）

        Returns:
            完整的提示词字符串
        """
        # 读取 tester.md 作为额外指令
        tester_content = self._get_tester_md()
        extra_instructions_parts: List[str] = []

        if tester_content:
            extra_instructions_parts.append(
                f"请参考以下测试用例编写规范（tester.md）：\n\n{tester_content}"
            )

        if batch_label:
            extra_instructions_parts.append(f"\n\n当前批次: {batch_label}")

        extra_instructions_parts.append(
            "\n注意：除正常用例外的所有字段值请优先使用 `ai_generated` 模式，"
            "仅在确实需要用户输入业务关键数据时使用 `user_input`。"
        )

        # --- 用户偏好记忆注入（Phase 3） ---
        try:
            preference_hints = self._build_preference_hints(endpoints)
            if preference_hints:
                extra_instructions_parts.append(f"\n\n{preference_hints}")
                logger.info("已注入用户偏好提示到 prompt")
        except Exception as e:
            logger.warning("构建偏好提示失败（不影响主流程）: %s", e)

        # --- few-shot 示例注入（Phase 3 记忆系统） ---
        task = self._get_task()
        few_shot = getattr(task, '_few_shot_examples', None)
        if few_shot:
            extra_instructions_parts.append(few_shot)
            logger.info("已注入 few-shot 示例到 prompt")

        # --- 评审反馈注入（WorkflowEngine 重试时使用） ---
        review_feedback = getattr(task, '_review_feedback', None)
        if review_feedback:
            extra_instructions_parts.append(f"\n\n{review_feedback}")
            logger.info("已注入评审反馈到 prompt")

        extra_instructions = "\n\n".join(extra_instructions_parts)

        # 构建端点 JSON 信息
        endpoints_json = json.dumps(endpoints, ensure_ascii=False, indent=2)

        # 获取 prompt 模板（优先 DB 配置，fallback 默认）
        prompt_template = self._get_prompt_template()

        prompt = prompt_template.replace(
            "{endpoints_json}", endpoints_json
        ).replace(
            "{extra_instructions}", extra_instructions
        )

        logger.debug(
            "生成的 prompt 长度: %d 字符", len(prompt)
        )
        return prompt

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM，复用 AIModelService。

        查找 role='api_import' 的 AIModelConfig，没有则 fallback 到 is_active=True。

        Args:
            prompt: 发送给 LLM 的提示词

        Returns:
            LLM 返回的原始文本响应

        Raises:
            RuntimeError: 未找到可用模型配置或 API 调用失败
        """
        from apps.requirement_analysis.models import AIModelConfig, AIModelService

        # 优先查找 role='api_import' 的配置
        config = AIModelConfig.objects.filter(
            role="api_import", is_active=True
        ).first()

        # fallback: 任意 is_active=True 的配置
        if config is None:
            config = AIModelConfig.objects.filter(is_active=True).first()

        if config is None:
            raise RuntimeError("未找到可用的 AI 模型配置，请在后台配置至少一个模型")

        logger.info(
            "使用模型配置: %s (role=%s, model=%s)",
            config.name, config.role, config.model_name,
        )

        messages = [
            {"role": "system", "content": "你是 API 测试用例生成专家，严格按用户要求的 JSON 格式输出，不要包含多余的解释。"},
            {"role": "user", "content": prompt},
        ]

        try:
            response = asyncio.run(
                AIModelService.call_openai_compatible_api(config, messages, max_tokens=8192)
            )
            content = response["choices"][0]["message"]["content"]
            logger.info(
                "LLM 调用成功，响应长度: %d 字符", len(content)
            )
            logger.warning(
                "LLM 响应前500字符: %s", content[:500]
            )
            logger.warning(
                "LLM 响应后300字符: %s", content[-300:]
            )
            return content
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}")

    def _parse_llm_response(self, raw_response: str) -> List[dict]:
        """解析 LLM 的 JSON 响应。

        尝试直接解析，如果失败则尝试从 markdown 代码块中提取。

        Args:
            raw_response: LLM 返回的原始文本

        Returns:
            解析后的 list[dict]

        Raises:
            ValueError: 无法解析 JSON
        """
        content = raw_response.strip()

        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取（```json ... ```）
        import re
        json_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        match = re.search(json_pattern, content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试从第一个 [ 到最后一个 ] 提取，并清理常见问题
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            json_str = content[start: end + 1]
            # 清理：去除尾随逗号（LLM 常在最后一个元素后加逗号）
            json_str = re.sub(r",\s*]", "]", json_str)
            json_str = re.sub(r",\s*}", "}", json_str)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        raise ValueError("无法解析 LLM 输出为 JSON 数组")

    def _generate_batch(self, endpoints: List[dict], batch_num: int) -> List[dict]:
        """生成一批端点的测试用例（含重试逻辑），沿途发射 SSE 事件。

        Args:
            endpoints: 一批端点列表
            batch_num: 批次编号

        Returns:
            校验通过的测试用例列表

        Raises:
            RuntimeError: 重试耗尽
        """
        batch_label = f"（第 {batch_num} 批）" if batch_num > 0 else ""

        # 发射批次开始事件
        self._emit('batch_start', {
            'batch_num': batch_num,
            'endpoints': [
                f"{ep.get('method', '')} {ep.get('path', '')}"
                for ep in endpoints
            ],
        })
        batch_start_time = time.time()

        prompt = self._build_prompt(endpoints, batch_label)

        last_error: Optional[str] = None
        for attempt in range(1, MAX_RETRIES + 2):  # 最多 MAX_RETRIES + 1 次
            try:
                logger.info(
                    "批次 %d 第 %d 次尝试: 调用 LLM", batch_num, attempt
                )

                # 发射 endpoint 级进度
                for ep in endpoints:
                    ep_key = f"{ep.get('method', '')} {ep.get('path', '')}"
                    self._emit('endpoint_progress', {
                        'endpoint_key': ep_key,
                        'status': 'processing',
                    })

                raw_response = self._call_llm(prompt)
                cases = self._parse_llm_response(raw_response)
                validated = validate_generator_output(cases)

                # 发射 endpoint 完成事件
                for ep in endpoints:
                    ep_key = f"{ep.get('method', '')} {ep.get('path', '')}"
                    ep_cases = [c for c in validated
                                if c.get('method', '') == ep.get('method', '')
                                and c.get('url', '') == ep.get('path', '')]
                    self._emit('endpoint_progress', {
                        'endpoint_key': ep_key,
                        'status': 'done',
                        'case_count': len(ep_cases),
                    })

                elapsed = time.time() - batch_start_time
                logger.info(
                    "批次 %d 第 %d 次尝试成功: %d 条用例通过校验 (%.1fs)",
                    batch_num, attempt, len(validated), elapsed,
                )

                # 发射批次完成事件
                self._emit('batch_complete', {
                    'batch_num': batch_num,
                    'total_cases': len(validated),
                    'duration_sec': round(elapsed, 1),
                })
                return validated

            except (ValueError, RuntimeError) as e:
                last_error = str(e)
                logger.warning(
                    "批次 %d 第 %d 次尝试失败: %s",
                    batch_num, attempt, last_error,
                )

                # 发射错误事件（可恢复）
                self._emit('batch_error', {
                    'batch_num': batch_num,
                    'error': last_error,
                    'retry_num': attempt,
                    'recoverable': attempt <= MAX_RETRIES,
                })

                if attempt <= MAX_RETRIES:
                    # 重试时在 prompt 中附加错误信息
                    retry_note = (
                        f"\n\n---\n⚠️ 前一次输出校验失败，请修正以下问题后重新生成：\n{last_error}\n"
                        f"请确保输出是合法的 JSON 数组，每个元素包含 name/method/url/body 等字段，"
                        f"且 body/params/headers 中的字段值使用 _mode 标记格式。"
                        f"特别提醒：每个 _mode 标记都必须包含 value 字段。"
                    )
                    prompt = prompt + retry_note
                else:
                    raise RuntimeError(
                        f"生成测试用例失败（已重试 {MAX_RETRIES} 次）: {last_error}"
                    )

        # 不应到达这里
        raise RuntimeError(f"生成测试用例失败: {last_error}")
