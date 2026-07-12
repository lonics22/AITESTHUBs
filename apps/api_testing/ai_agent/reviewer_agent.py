"""Reviewer Agent — 质量门禁，对 Generator 输出的用例进行语义评审

评审维度：
  1. 覆盖率（40 分）：每个端点是否都有 normal + error 用例
  2. 断言完整性（30 分）：每条用例是否有充分的断言
  3. 数据合理性（30 分）：ai_generated 值是否合理、user_input 是否有 _label

使用方法:
    reviewer = ReviewerAgent(task_id)
    report = reviewer.review(generated_cases, endpoints)
    if report.passed:
        # 用例质量合格
    else:
        # 注入 feedback.retry_prompt_snippet 到 Generator prompt 重试
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.api_testing.ai_agent.schemas import (
    FeedbackItem,
    FeedbackPayload,
    ReviewReport,
)
from apps.api_testing.ai_agent.schema import validate_generator_output

logger = logging.getLogger(__name__)


class ReviewerAgent:
    """质量门禁：对 Generator 输出的用例进行语义评审"""

    # 评分阈值
    PASS_THRESHOLD = 60          # 通过阈值
    PERFECT_THRESHOLD = 90       # 完美阈值
    MAX_REVIEW_LOOPS = 1         # 最大回退次数

    def __init__(self, task_id: int):
        self.task_id = task_id

    def review(
        self,
        cases: List[dict],
        endpoints: Optional[List[dict]] = None,
    ) -> ReviewReport:
        """执行全量评审

        先过 validate_generator_output() 硬门禁，再做语义评分。

        Args:
            cases: Generator 输出的测试用例列表
            endpoints: 原始端点列表（用于计算覆盖率）

        Returns:
            ReviewReport: 评审报告
        """
        # --- 硬门禁：Schema 校验 ---
        try:
            validate_generator_output(cases)
        except ValueError as e:
            logger.warning("Reviewer: schema 校验不通过: %s", e)
            report = ReviewReport(passed=False, total_score=0.0)
            report.feedback.add_item(
                type='missing_coverage',
                severity='error',
                suggestion=f'Schema 校验失败: {e}',
            )
            return report

        # --- 软门禁：语义评分 ---
        coverage_score = self._check_coverage(cases, endpoints or [])
        assertion_score = self._check_assertions(cases)
        data_score = self._check_data_reasonableness(cases)

        total_score = coverage_score + assertion_score + data_score
        passed = total_score >= self.PASS_THRESHOLD

        # 生成反馈
        feedback = self._generate_feedback(
            cases, endpoints or [],
            coverage_score, assertion_score, data_score,
        )

        report = ReviewReport(
            passed=passed,
            total_score=total_score,
            coverage_score=coverage_score,
            assertion_score=assertion_score,
            data_score=data_score,
            feedback=feedback,
        )

        logger.info(
            "Reviewer: task=%d score=%.1f passed=%s coverage=%.1f assertion=%.1f data=%.1f",
            self.task_id, total_score, passed,
            coverage_score, assertion_score, data_score,
        )

        return report

    # ------------------------------------------------------------------
    # 覆盖率评分 (0-40)
    # ------------------------------------------------------------------

    def _check_coverage(self, cases: List[dict],
                        endpoints: List[dict]) -> float:
        """覆盖率评分

        - 每个端点至少 1 个 normal 用例: +15 分（按比例）
        - 每个端点至少 1 个 error 用例:  +15 分（按比例）
        - 所有端点都有用例:              +10 分（按比例）
        """
        if not endpoints:
            return 30.0  # 没有端点信息时给基础分

        endpoint_keys = {f"{ep.get('method', '')} {ep.get('path', '')}"
                         for ep in endpoints}
        if not endpoint_keys:
            return 30.0

        covered_normal = set()
        covered_error = set()
        covered_any = set()

        for case in cases:
            key = f"{case.get('method', '')} {case.get('url', '')}"
            case_type = case.get('_case_type', 'normal')
            if case_type == 'normal':
                covered_normal.add(key)
            elif case_type == 'error':
                covered_error.add(key)
            covered_any.add(key)

        total = len(endpoint_keys)
        normal_ratio = len(covered_normal) / total
        error_ratio = len(covered_error) / total
        any_ratio = len(covered_any) / total

        score = min(
            15.0 * normal_ratio + 15.0 * error_ratio + 10.0 * any_ratio,
            40.0,
        )
        return score

    # ------------------------------------------------------------------
    # 断言评分 (0-30)
    # ------------------------------------------------------------------

    def _check_assertions(self, cases: List[dict]) -> float:
        """断言完整性评分

        - 每条用例至少 1 条断言:       +10 分（按比例）
        - 每条用例有 status_code 断言: +10 分（按比例）
        - 超过 50% 有 json_path 断言:  +10 分（按比例）
        """
        if not cases:
            return 0.0

        n = len(cases)
        has_any = sum(1 for c in cases if c.get('assertions'))
        has_status = sum(
            1 for c in cases
            if any(a.get('type') == 'status_code'
                   for a in c.get('assertions', []))
        )
        has_json = sum(
            1 for c in cases
            if any(a.get('type') == 'json_path'
                   for a in c.get('assertions', []))
        )

        score = 0.0
        score += 10.0 * (has_any / n)
        score += 10.0 * (has_status / n)
        # json_path: 超过 50% 的线即满分
        json_ratio = min(has_json / max(n * 0.5, 1), 1.0)
        score += 10.0 * json_ratio

        return min(score, 30.0)

    # ------------------------------------------------------------------
    # 数据合理性评分 (0-30)
    # ------------------------------------------------------------------

    def _check_data_reasonableness(self, cases: List[dict]) -> float:
        """数据合理性评分

        - ai_generated 字段值不为空/占位符: +15 分（按比例）
        - user_input 字段有 _label 提示:     +10 分（按比例）
        - error 用例使用合理的边界值:        +5 分（按比例）
        """
        if not cases:
            return 0.0

        total_mode_fields = 0
        valid_ai_fields = 0
        labeled_user_fields = 0
        error_cases = 0
        good_error_cases = 0

        for case in cases:
            is_error = case.get('_case_type') == 'error'
            if is_error:
                error_cases += 1

            # 遍历 body/params/headers 中的 _mode 字段
            for section in ('body', 'params', 'headers'):
                data = case.get(section, {})
                if isinstance(data, dict):
                    # body 有 {type, data} 包装
                    if section == 'body' and 'data' in data:
                        fields = data['data']
                    else:
                        fields = data

                    if not isinstance(fields, dict):
                        continue

                    for field_name, field_val in fields.items():
                        if not isinstance(field_val, dict):
                            continue
                        if '_mode' not in field_val:
                            continue

                        total_mode_fields += 1
                        mode = field_val['_mode']

                        if mode == 'ai_generated':
                            val = field_val.get('value')
                            # 非空且不是纯占位符
                            if val is not None and str(val).strip():
                                valid_ai_fields += 1

                        elif mode == 'user_input':
                            if field_val.get('_label'):
                                labeled_user_fields += 1

                        # 错误用例边界值检查
                        if is_error and mode == 'ai_generated':
                            val = field_val.get('value')
                            if val is not None and str(val).strip():
                                good_error_cases += 1

        # 计算分数
        ai_score = 0.0
        if total_mode_fields > 0:
            ai_score = 15.0 * (valid_ai_fields / max(total_mode_fields, 1))

        label_score = 0.0
        if total_mode_fields > 0:
            label_score = 10.0 * (labeled_user_fields / max(total_mode_fields, 1))

        error_score = 0.0
        if error_cases > 0:
            error_score = 5.0 * (good_error_cases / max(error_cases, 1))
        elif total_mode_fields > 0:
            # 没有 error 用例不扣分，但也不加分
            error_score = 2.5

        return min(ai_score + label_score + error_score, 30.0)

    # ------------------------------------------------------------------
    # 反馈生成
    # ------------------------------------------------------------------

    def _generate_feedback(
        self,
        cases: List[dict],
        endpoints: List[dict],
        coverage_score: float,
        assertion_score: float,
        data_score: float,
    ) -> FeedbackPayload:
        """生成结构化反馈"""
        feedback = FeedbackPayload()

        # --- 覆盖率问题 ---
        if coverage_score < 30.0:
            endpoint_keys = {f"{ep.get('method', '')} {ep.get('path', '')}"
                             for ep in endpoints}
            covered_normal = set()
            covered_error = set()
            for case in cases:
                key = f"{case.get('method', '')} {case.get('url', '')}"
                ct = case.get('_case_type', 'normal')
                if ct == 'normal':
                    covered_normal.add(key)
                elif ct == 'error':
                    covered_error.add(key)

            for ek in sorted(endpoint_keys):
                if ek not in covered_normal:
                    feedback.add_item(
                        type='missing_coverage',
                        severity='warning',
                        endpoint=ek,
                        suggestion=f'为 {ek} 补充一条 normal 类型的测试用例',
                    )
                if ek not in covered_error:
                    feedback.add_item(
                        type='missing_coverage',
                        severity='warning',
                        endpoint=ek,
                        suggestion=f'为 {ek} 补充一条 error 类型的测试用例（空值/非法值/边界值）',
                    )

        # --- 断言问题 ---
        if assertion_score < 20.0:
            for i, case in enumerate(cases):
                assertions = case.get('assertions', [])
                if not assertions:
                    feedback.add_item(
                        type='missing_assertion',
                        severity='error',
                        case_index=i,
                        suggestion=f'用例 #{i} ({case.get("name", "")}) 缺少断言，请添加至少一条断言',
                    )
                elif not any(a.get('type') == 'status_code'
                             for a in assertions):
                    feedback.add_item(
                        type='missing_assertion',
                        severity='warning',
                        case_index=i,
                        suggestion=f'用例 #{i} ({case.get("name", "")}) 缺少 status_code 断言',
                    )

        # --- 数据合理性问题 ---
        if data_score < 20.0:
            for i, case in enumerate(cases):
                for section in ('body', 'params', 'headers'):
                    data = case.get(section, {})
                    if isinstance(data, dict):
                        if section == 'body' and 'data' in data:
                            fields = data['data']
                        else:
                            fields = data
                        if not isinstance(fields, dict):
                            continue

                        for fname, fval in fields.items():
                            if not isinstance(fval, dict):
                                continue
                            mode = fval.get('_mode')
                            if mode == 'user_input' and not fval.get('_label'):
                                feedback.add_item(
                                    type='label_missing',
                                    severity='info',
                                    case_index=i,
                                    field=fname,
                                    suggestion=f'字段 "{fname}" 为 user_input 但缺少 _label 提示',
                                )
                            elif mode == 'ai_generated':
                                val = fval.get('value')
                                if val is None or not str(val).strip():
                                    feedback.add_item(
                                        type='data_issue',
                                        severity='warning',
                                        case_index=i,
                                        field=fname,
                                        suggestion=f'字段 "{fname}" 的 ai_generated 值为空，请填充合理值',
                                    )

        # --- 生成摘要和 retry_snippet ---
        error_items = [it for it in feedback.items if it.severity == 'error']
        warning_items = [it for it in feedback.items if it.severity == 'warning']

        parts = [f'评审评分: {coverage_score + assertion_score + data_score:.0f}/100']
        if error_items:
            parts.append(f'严重问题: {len(error_items)} 个')
        if warning_items:
            parts.append(f'警告: {len(warning_items)} 个')
        feedback.summary = '; '.join(parts)

        # 构造 retry_prompt_snippet — 可直接注入 Generator 的 prompt
        if not feedback.items:
            feedback.retry_prompt_snippet = ''
        else:
            lines = ['\n---', '⚠️ 评审反馈，请修正以下问题：\n']
            for idx, item in enumerate(feedback.items, 1):
                endpoint_str = f' [{item.endpoint}]' if item.endpoint else ''
                case_str = f' (用例 #{item.case_index})' if item.case_index is not None else ''
                field_str = f' 字段 "{item.field}"' if item.field else ''
                lines.append(
                    f'{idx}. [{item.type}]{endpoint_str}{case_str}{field_str}'
                )
                lines.append(f'   → {item.suggestion}')
            lines.append('\n请基于以上反馈重新生成。')
            feedback.retry_prompt_snippet = '\n'.join(lines)

        return feedback
