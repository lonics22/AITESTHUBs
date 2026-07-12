"""记忆系统 — 短期记忆（Cache）+ 长期偏好 + 案例记忆（Phase 3）

组件:
- SessionMemory:         Django Cache 实现的短期会话记忆
- get_user_preference:   用户字段 _mode 偏好查询
- record_user_preference: 用户字段 _mode 偏好记录
- MemoryAugmentedPromptBuilder: 从 GenerationMemory 检索 few-shot 示例
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from django.contrib.auth import get_user_model

from apps.api_testing.models import AIImportTask, GenerationMemory, UserImportPreference

logger = logging.getLogger(__name__)

User = get_user_model()

# ---------------------------------------------------------------------------
# 短期记忆（Django Cache）
# ---------------------------------------------------------------------------

# 偏好查询信任阈值：出现次数 >= 此值时偏好生效
PREFERENCE_CONFIDENCE_THRESHOLD = 3

# SessionMemory TTL
SESSION_TTL = 3600  # 1 小时


class SessionMemory:
    """短期记忆：当前会话上下文，Django Cache 存储

    存储内容：
    - 当前任务状态、阶段
    - 重试历史（失败的批次数和原因）
    - 当前批次的进度

    用法:
        mem = SessionMemory(task_id)
        mem.update(status='generating', batch_num=1)
        print(mem.get('status'))
    """

    def __init__(self, task_id: int):
        self.task_id = task_id
        self._cache_key = f"ai_import_session_{task_id}"
        self._data = self._load()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, **kwargs):
        """更新会话记忆并持久化到 Cache"""
        self._data.update(kwargs)
        self._save()

    def clear(self):
        """清除会话记忆"""
        from django.core.cache import cache
        cache.delete(self._cache_key)
        self._data = {}

    def get_retry_history(self) -> List[Dict[str, Any]]:
        """获取重试历史"""
        return self._data.get('retry_history', [])

    def add_retry(self, batch_num: int, reason: str):
        """添加一次重试记录"""
        history = self._data.setdefault('retry_history', [])
        history.append({
            'batch_num': batch_num,
            'reason': reason,
        })
        self._save()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        from django.core.cache import cache
        data = cache.get(self._cache_key)
        if data is None:
            data = self._init_from_db()
            cache.set(self._cache_key, data, SESSION_TTL)
        return data

    def _save(self):
        from django.core.cache import cache
        cache.set(self._cache_key, self._data, SESSION_TTL)

    def _init_from_db(self) -> dict:
        try:
            task = AIImportTask.objects.get(id=self.task_id)
            return {
                'status': task.status,
                'endpoint_count': len(task.parsed_endpoints or []),
                'last_phase': None,
                'retry_history': [],
            }
        except AIImportTask.DoesNotExist:
            return {
                'status': 'unknown',
                'endpoint_count': 0,
                'last_phase': None,
                'retry_history': [],
            }


# ---------------------------------------------------------------------------
# 长期偏好：字段 _mode 偏好查询和记录
# ---------------------------------------------------------------------------


def get_user_preference(
    user_id: int,
    field_name: str,
    endpoint_path: str = '',
) -> Optional[str]:
    """查询用户对特定字段的 _mode 偏好。

    优先精确匹配（user + field_name + endpoint_pattern），
    再模糊匹配（user + field_name + 空 endpoint_pattern）。

    只有当 count >= PREFERENCE_CONFIDENCE_THRESHOLD 时才生效，
    避免偶然的偏好被过度使用。

    Args:
        user_id: 用户 ID
        field_name: 字段名（小写）
        endpoint_path: 端点路径（如 /users/{id}）

    Returns:
        'user_input' | 'ai_generated' | 'ask_user' | None
    """
    try:
        # 优先精确匹配
        pref = UserImportPreference.objects.filter(
            user_id=user_id,
            field_name__iexact=field_name,
            endpoint_pattern=endpoint_path,
            count__gte=PREFERENCE_CONFIDENCE_THRESHOLD,
        ).order_by('-count', '-last_used').first()

        # 再模糊匹配（无特定端点）
        if pref is None:
            pref = UserImportPreference.objects.filter(
                user_id=user_id,
                field_name__iexact=field_name,
                endpoint_pattern='',
                count__gte=PREFERENCE_CONFIDENCE_THRESHOLD,
            ).order_by('-count', '-last_used').first()

        if pref:
            logger.info(
                "用户偏好: field=%s mode=%s (x%d)",
                field_name, pref.preferred_mode, pref.count,
            )
            return pref.preferred_mode

    except Exception as e:
        logger.warning("查询用户偏好失败: %s", e)

    return None


def record_user_preference(
    user_id: int,
    field_name: str,
    preferred_mode: str,
    endpoint_path: str = '',
):
    """记录或更新用户的 _mode 偏好。

    调用方：SaverAgent 在保存用例时调用。

    Args:
        user_id: 用户 ID
        field_name: 字段名
        preferred_mode: 用户选择的模式
        endpoint_path: 端点路径
    """
    try:
        obj, created = UserImportPreference.objects.get_or_create(
            user_id=user_id,
            field_name__iexact=field_name,
            endpoint_pattern=endpoint_path,
            defaults={
                'preferred_mode': preferred_mode,
                'count': 1,
            },
        )
        if not created:
            # 如果模式变了，重置计数
            if obj.preferred_mode != preferred_mode:
                obj.preferred_mode = preferred_mode
                obj.count = 1
            else:
                obj.count += 1
            obj.save(update_fields=['preferred_mode', 'count', 'last_used'])

        logger.debug(
            "记录偏好: user=%d field=%s mode=%s (endpoint=%s) %s",
            user_id, field_name, preferred_mode, endpoint_path,
            'created' if created else 'updated',
        )
    except Exception as e:
        logger.warning("记录用户偏好失败: %s", e)


# ---------------------------------------------------------------------------
# 案例记忆：few-shot 示例检索
# ---------------------------------------------------------------------------


class MemoryAugmentedPromptBuilder:
    """基于记忆增强的 Prompt 构建器

    从 GenerationMemory 检索与当前端点相似的成功案例作为 few-shot 示例，
    注入到生成 prompt 中。
    """

    DEFAULT_TOP_K = 3

    def __init__(self, task_id: int):
        self.task_id = task_id

    def retrieve_few_shot(
        self,
        user_id: int,
        endpoints: List[dict],
        top_k: int = DEFAULT_TOP_K,
    ) -> List[dict]:
        """检索与当前端点相似的成功案例作为 few-shot。

        检索策略：通过端点 tag 匹配，取评分最高的 top_k 条记忆。

        Args:
            user_id: 用户 ID
            endpoints: 当前待生成的端点列表
            top_k: 最多返回的示例数

        Returns:
            成功案例的 generated_output 列表
        """
        if not endpoints:
            return []

        # 收集所有 tag
        tags = set()
        for ep in endpoints:
            for tag in ep.get('tags', []):
                if tag:
                    tags.add(str(tag))

        if not tags:
            # 没有 tag 时，按 endpoint_key 的前缀匹配
            paths = [ep.get('path', '') for ep in endpoints if ep.get('path')]
            if not paths:
                return []
            memories = GenerationMemory.objects.filter(
                user_id=user_id,
                was_successful=True,
                review_score__gte=70.0,
            ).filter(
                # 路径前缀匹配
                # 使用 endpoint_key 的前 N 个字符匹配
                endpoint_key__in=[
                    f"{ep.get('method', '')} {ep.get('path', '')}"
                    for ep in endpoints
                ],
            ).order_by('-review_score')[:top_k]

            return [m.generated_output for m in memories]

        # 按 tag 重叠匹配
        memories = GenerationMemory.objects.filter(
            user_id=user_id,
            was_successful=True,
            review_score__gte=70.0,
        ).order_by('-review_score')

        scored = []
        for mem in memories:
            mem_tags = set(mem.endpoint_tags or [])
            overlap = len(tags & mem_tags)
            if overlap > 0:
                scored.append((overlap, mem))

        # 按重叠度排序，取 top_k
        scored.sort(key=lambda x: -x[0])
        return [m.generated_output for _, m in scored[:top_k]]

    def inject_few_shot(self, template: str, examples: List[dict]) -> str:
        """将 few-shot 示例注入 prompt 模板。

        在模板中找到 {few_shot_examples} 占位符并替换。
        如果没有该占位符，追加到末尾。

        Args:
            template: 原始 prompt 模板
            examples: few-shot 示例列表

        Returns:
            注入后的 prompt 字符串
        """
        if not examples:
            return template.replace('{few_shot_examples}', '')

        section = (
            "\n\n### 参考示例（之前成功的生成）:\n\n"
            f"{json.dumps(examples, ensure_ascii=False, indent=2)}"
        )

        if '{few_shot_examples}' in template:
            return template.replace('{few_shot_examples}', section)

        # 没有占位符时追加到末尾
        return template + section
