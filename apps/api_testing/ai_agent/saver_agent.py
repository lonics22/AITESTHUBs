"""Saver Agent — 持久化用例到 DB + 同步记忆系统（Phase 3）

功能：
1. 接收前端填完的用例列表
2. 调用 frontend_to_db() 转换 _mode 标记为实际值
3. 按标签分组创建 ApiCollection
4. 批量创建 ApiRequest
5. 同步偏好到 UserImportPreference
6. 存储成功案例到 GenerationMemory

当前 Phase 3 聚焦于记忆同步逻辑，实际的保存流程仍在 views.py 中。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.api_testing.ai_agent.memory import record_user_preference
from apps.api_testing.models import AIImportTask, GenerationMemory

logger = logging.getLogger(__name__)

User = get_user_model()


class SaverAgent:
    """持久化 Agent：保存用例到 DB + 同步记忆系统

    用法:
        saver = SaverAgent(task, user)
        saver.save(cases)
    """

    def __init__(self, task: AIImportTask, user):
        self.task = task
        self.user = user
        self._endpoint_map: Dict[str, dict] = self._build_endpoint_map()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def sync_memory(self, cases: List[dict], review_score: float = 0.0):
        """保存成功后同步记忆系统。

        Args:
            cases: 用户已编辑并保存的用例列表
            review_score: 评审评分（如适用）
        """
        # 1. 记录用户对字段 _mode 的偏好
        self._record_mode_preferences(cases)

        # 2. 存储成功案例到 GenerationMemory
        self._store_success_cases(cases, review_score)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_endpoint_map(self) -> Dict[str, dict]:
        """构建 endpoint_key → endpoint 的映射"""
        mapping = {}
        for ep in self.task.parsed_endpoints or []:
            key = f"{ep.get('method', '')} {ep.get('path', '')}"
            mapping[key] = ep
        return mapping

    def _get_endpoint_for_case(self, case: dict) -> Optional[dict]:
        """通过 method + url 找到对应的原始端点"""
        key = f"{case.get('method', '')} {case.get('url', '')}"
        return self._endpoint_map.get(key)

    def _record_mode_preferences(self, cases: List[dict]):
        """从保存的用例中提取用户对字段 _mode 的偏好。

        对比：
        - 用户最终选择 user_input → 记录该偏好
        - ai_generated 字段保持不变 → 不记录（说明 AI 选对了）
        """
        if not self.user or not self.user.id:
            return

        recorded = 0
        for case in cases:
            ep = self._get_endpoint_for_case(case)
            endpoint_path = ep.get('path', '') if ep else ''

            for section in ('body', 'params', 'headers'):
                data = case.get(section, {})
                if section == 'body' and isinstance(data, dict) and 'data' in data:
                    fields = data['data']
                else:
                    fields = data

                if not isinstance(fields, dict):
                    continue

                for field_name, field_val in fields.items():
                    if not isinstance(field_val, dict):
                        continue
                    mode = field_val.get('_mode', '')
                    if mode == 'user_input':
                        # 用户选择了 user_input → 记录偏好
                        record_user_preference(
                            user_id=self.user.id,
                            field_name=field_name,
                            preferred_mode='user_input',
                            endpoint_path=endpoint_path,
                        )
                        recorded += 1

        if recorded > 0:
            logger.info(
                "SaverAgent: 记录了 %d 个 user_input 偏好 for user %s",
                recorded, self.user,
            )

    def _store_success_cases(self, cases: List[dict], review_score: float):
        """将成功的生成案例存入 GenerationMemory 供后来 few-shot 使用。

        仅当 review_score >= 70 时存储（高质量案例才有参考价值）。
        每个端点分组存储一份完整的输出。
        """
        if review_score < 70.0:
            logger.info(
                "SaverAgent: 评分 %.1f < 70，不存储案例", review_score,
            )
            return

        if not self.user or not self.user.id:
            return

        # 按端点分组
        endpoint_cases: Dict[str, List[dict]] = {}
        for case in cases:
            key = f"{case.get('method', '')} {case.get('url', '')}"
            endpoint_cases.setdefault(key, []).append(case)

        stored = 0
        for key, group_cases in endpoint_cases.items():
            ep = self._endpoint_map.get(key, {})

            GenerationMemory.objects.create(
                task=self.task,
                user=self.user,
                endpoint_key=key,
                endpoint_tags=ep.get('tags', []),
                was_successful=True,
                review_score=review_score,
                generated_output=group_cases,
            )
            stored += 1

        logger.info(
            "SaverAgent: 存储了 %d 条案例记忆 (评分 %.1f)",
            stored, review_score,
        )

    @transaction.atomic
    def _batch_create_requests(self, cases: List[dict]) -> List[int]:
        """批量创建 ApiRequest（原子操作）

        Args:
            cases: frontend_to_db 转换后的用例列表

        Returns:
            创建的 ApiRequest ID 列表
        """
        # 保留给 Phase 3 完整集成，当前保存逻辑在 views.py 中
        raise NotImplementedError("SaverAgent 完整保存逻辑将在后续 Phase 集成")
