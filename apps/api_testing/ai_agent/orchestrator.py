"""WorkflowEngine — 增强型工作流编排器

负责编排 Parser → Generator → Reviewer → Saver 四阶段工作流。
当前 Phase 1 只实现 Generator + Reviewer 的编排。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from apps.api_testing.models import AIImportTask
from apps.api_testing.ai_agent.generator import AnalyzeGenerator
from apps.api_testing.ai_agent.reviewer_agent import ReviewerAgent
from apps.api_testing.ai_agent.memory import MemoryAugmentedPromptBuilder

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """工作流编排器

    用法:
        engine = WorkflowEngine(task_id)
        engine.on('phase', lambda d: ...)     # 注册事件监听
        result = engine.run()                  # 执行完整工作流
    """

    def __init__(self, task_id: int):
        self.task_id = task_id
        self._task: Optional[AIImportTask] = None
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 事件系统
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
                logger.warning("事件处理器异常 (%s): %s", event, e)

    # ------------------------------------------------------------------
    # 核心流程
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """执行完整工作流

        Phase 1 实现:
          1. Generator: 调用 AnalyzeGenerator 生成用例
          2. Reviewer:  评审生成的用例质量
          3. 回退: 评分不足时，注入反馈重试（最多 1 次）

        Returns:
            dict: {
                'generated_cases': [...],
                'review_report': ReviewReport,
                'review_passed': bool,
            }
        """
        task = self._get_task()

        # --- Phase 2: Generate ---
        self._emit('phase', {'phase': 'generating'})
        logger.info("WorkflowEngine[%d]: 开始生成", self.task_id)

        generator = AnalyzeGenerator(self.task_id)

        # 注入 few-shot 示例（Phase 3 记忆系统）
        try:
            builder = MemoryAugmentedPromptBuilder(self.task_id)
            user_id = getattr(task.created_by, 'id', None)
            if user_id:
                examples = builder.retrieve_few_shot(
                    user_id, task.parsed_endpoints or [], top_k=2,
                )
                if examples:
                    task._few_shot_examples = builder.inject_few_shot('', examples)
                    logger.info(
                        "已注入 %d 个 few-shot 示例到 prompt", len(examples)
                    )
        except Exception as e:
            logger.warning("注入 few-shot 失败（不影响主流程）: %s", e)

        # 转发 Generator 事件到 WorkflowEngine 事件系统（Phase 2 SSE 支持）
        generator.on('batch_start', lambda d: self._emit('batch_start', d))
        generator.on('batch_complete', lambda d: self._emit('batch_complete', d))
        generator.on('endpoint_progress', lambda d: self._emit('endpoint_progress', d))
        generator.on('batch_error', lambda d: self._emit('batch_error', d))

        generated_cases = generator.generate()

        self._emit('phase', {'phase': 'reviewing'})
        logger.info("WorkflowEngine[%d]: 生成完成, 共%d条用例，开始评审",
                     self.task_id, len(generated_cases))

        # --- Phase 3: Review ---
        reviewer = ReviewerAgent(self.task_id)
        endpoints = task.parsed_endpoints or []
        report = reviewer.review(generated_cases, endpoints)

        self._emit('review_result', {
            'passed': report.passed,
            'total_score': report.total_score,
            'coverage_score': report.coverage_score,
            'assertion_score': report.assertion_score,
            'data_score': report.data_score,
        })

        # --- 回退逻辑：评审不通过时重试一次 ---
        retry_count = getattr(task, '_retry_count', 0)
        if not report.passed and retry_count < ReviewerAgent.MAX_REVIEW_LOOPS:
            logger.info(
                "WorkflowEngine[%d]: 评审未通过(%.1f分)，准备回退重试",
                self.task_id, report.total_score,
            )
            self._emit('phase', {'phase': 'retrying'})

            # 注入反馈到 prompt，重新生成
            # 通过修改 task 的临时属性传递反馈
            task._review_feedback = report.feedback.retry_prompt_snippet
            task._retry_count = retry_count + 1

            generator = AnalyzeGenerator(self.task_id)
            generated_cases = generator.generate()

            # 重新评审
            report = reviewer.review(generated_cases, endpoints)
            self._emit('review_result', {
                'passed': report.passed,
                'total_score': report.total_score,
                'coverage_score': report.coverage_score,
                'assertion_score': report.assertion_score,
                'data_score': report.data_score,
            })

            logger.info(
                "WorkflowEngine[%d]: 回退后评分 %.1f passed=%s",
                self.task_id, report.total_score, report.passed,
            )

        # --- 完成 ---
        self._emit('phase', {'phase': 'complete'})

        return {
            'generated_cases': generated_cases,
            'review_report': {
                'passed': report.passed,
                'total_score': report.total_score,
                'coverage_score': report.coverage_score,
                'assertion_score': report.assertion_score,
                'data_score': report.data_score,
                'feedback_summary': report.feedback.summary,
                'feedback_count': len(report.feedback.items),
            },
            'review_passed': report.passed,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_task(self) -> AIImportTask:
        if self._task is None:
            self._task = AIImportTask.objects.get(id=self.task_id)
        return self._task
