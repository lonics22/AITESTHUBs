"""确定性 _mode 决策规则引擎 — 将 _mode 分配从 LLM 迁移到代码层

核心思路：
- _mode 决策是结构化问题，不需要 LLM 参与
- 通过规则 + 用户偏好覆盖，实现稳定、可审计、零 Token 成本的决策
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 规则常量
# ---------------------------------------------------------------------------

# 自动生成参数模式（LLM 可以确定值的字段）
AUTO_PARAM_PATTERNS = frozenset({
    'page', 'limit', 'size', 'offset', 'per_page',
    'format', 'sort', 'order', 'fields',
    'timestamp', '_t', '_', 'callback',
    'locale', 'language', 'lang',
})

# 上下文引用参数（用户必须提供的业务数据）
CONTEXT_REF_PATTERNS = frozenset({
    'id', 'user_id', 'order_id', 'product_id',
    'email', 'phone', 'username', 'token',
    'session_id', 'device_id',
})

# 枚举参数（LLM 可以用枚举值填充）
ENUM_FIELD_NAMES = frozenset({
    'status', 'type', 'category', 'level', 'priority',
})


def decide_mode_strategy(
    field_name: str,
    is_required: bool = False,
    case_type: str = 'normal',
) -> str:
    """确定性决策：字段应该是什么 _mode

    优先级规则（从高到低）:
    1. 认证/安全相关字段 → user_input
    2. 匹配 CONTEXT_REF_PATTERNS → user_input
    3. 匹配 AUTO_PARAM_PATTERNS → ai_generated
    4. 有枚举值 → ai_generated
    5. 必需 body 字段且无默认值 → user_input
    6. 错误用例 → ai_generated（覆盖前面的规则）
    7. 兜底 → ai_generated

    Args:
        field_name: 字段名（大小写不敏感）
        is_required: 是否为必需字段
        case_type: 'normal' | 'error'

    Returns:
        'user_input' | 'ai_generated' | 'ask_user'
    """
    field_lower = field_name.lower().strip()

    # 1. 认证/安全相关 → user_input
    if field_lower in {'authorization', 'api_key', 'apikey', 'x-api-key',
                        'bearer', 'token', 'access_token', 'refresh_token'}:
        return 'user_input'

    # 2. 业务上下文引用 → user_input
    for pattern in CONTEXT_REF_PATTERNS:
        if field_lower == pattern or field_lower.endswith(f'_{pattern}'):
            return 'user_input'

    # 3. 自动生成参数 → ai_generated
    if field_lower in AUTO_PARAM_PATTERNS:
        return 'ai_generated'

    # 4. 枚举字段 → ai_generated
    if field_lower in ENUM_FIELD_NAMES:
        return 'ai_generated'

    # 5. 必需字段 → user_input
    if is_required:
        return 'user_input'

    # 6. 错误用例 → ai_generated（覆盖）
    if case_type == 'error':
        return 'ai_generated'

    # 7. 兜底
    return 'ai_generated'


def apply_user_preference(
    field_name: str,
    base_decision: str,
    user_preferred_mode: Optional[str],
) -> str:
    """用用户偏好覆盖基础决策。

    用户偏好只在出现 3 次以上时生效，且只覆盖 base_decision 为
    ai_generated 的字段（用户不会想把自己填过的字段改成 AI 生成）。

    Args:
        field_name: 字段名
        base_decision: decide_mode_strategy() 的结果
        user_preferred_mode: 从 UserImportPreference 查到的偏好

    Returns:
        最终 _mode 值
    """
    if user_preferred_mode is None:
        return base_decision

    # 只覆盖 ai_generated → user_input（用户想把 AI 填的改成自己填）
    if base_decision == 'ai_generated' and user_preferred_mode == 'user_input':
        return 'user_input'

    return base_decision
