"""LLM 输出 Schema 强制校验层 — 支持 _mode 标记"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

VALID_HTTP_METHODS = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
})

VALID_MODES = frozenset({"user_input", "ai_generated", "ask_user"})


class ApiRequestSchema(BaseModel):
    """LLM 生成的单条 API 请求，字段与 ApiRequest model 保持一致"""
    name: str = Field(..., max_length=200, description="请求名称")
    description: str = Field("", description="请求描述")
    method: str = Field("GET", description="请求方法")
    url: str = Field(..., description="请求 URL")
    headers: Dict[str, Any] = Field(default_factory=dict, description="请求头")
    params: Dict[str, Any] = Field(default_factory=dict, description="URL 查询参数")
    body: Dict[str, Any] = Field(default_factory=dict, description="请求体")
    auth: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "none"}, description="认证信息"
    )
    assertions: List[Dict[str, Any]] = Field(default_factory=list, description="断言列表")
    pre_request_script: str = Field("", description="请求前脚本")
    post_request_script: str = Field("", description="请求后脚本")

    # _case_type 由 validate_generator_output() 手动校验，这里不声明 Pydantic 字段
    # 以避免 Pydantic v2 不允许下划线开头的字段名


class BatchApiRequestsSchema(BaseModel):
    """LLM 输出的完整批次"""
    requests: List[ApiRequestSchema]
    total: int


def validate_llm_output(raw: List[dict]) -> List[dict]:
    """校验 LLM 输出的每一条记录，确保字段类型和必填项都符合 ApiRequest 模型。

    Args:
        raw: LLM 返回的原始 JSON 解析后的 list[dict]

    Returns:
        清理后的 list[dict]，可直接传给 ApiRequest.objects.create()

    Raises:
        ValueError: 包含具体字段错误信息
    """
    try:
        batch = BatchApiRequestsSchema(requests=raw, total=len(raw))
    except ValidationError as e:
        err = e.errors()[0]
        field = ".".join(str(loc) for loc in err["loc"])
        raise ValueError(
            f"字段 '{field}' 校验失败: {err['msg']}（输入值: {err.get('input', 'N/A')}）"
        )

    for i, req in enumerate(batch.requests):
        if req.method not in VALID_HTTP_METHODS:
            raise ValueError(
                f"请求 #{i} ('{req.name}'): method '{req.method}' 不是合法的 HTTP 方法"
            )
        if not req.url:
            raise ValueError(f"请求 #{i} ('{req.name}'): url 不能为空")
        if not req.name:
            raise ValueError(f"请求 #{i}: name 不能为空")
        if not isinstance(req.headers, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): headers 必须是键值对对象")
        if not isinstance(req.params, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): params 必须是键值对对象")
        if not isinstance(req.body, dict):
            raise ValueError(f"请求 #{i} ('{req.name}'): body 必须是对象")
        if not isinstance(req.auth, dict) or "type" not in req.auth:
            raise ValueError(f"请求 #{i} ('{req.name}'): auth 必须有 type 字段")

    logger.info("validate_llm_output: %d requests passed validation", len(batch.requests))
    return [req.model_dump(exclude_none=True) for req in batch.requests]


def _is_mode_value(value: Any) -> bool:
    """判断一个值是否是 _mode 标记格式。

    Args:
        value: 待检查的值

    Returns:
        如果是 {"_mode": ..., "value": ...} 格式返回 True
    """
    return (
        isinstance(value, dict)
        and "_mode" in value
        and "value" in value
        and value["_mode"] in VALID_MODES
    )


def _check_mode_structure(obj: Any, path: str = "") -> List[str]:
    """递归检查对象中所有 _mode 标记字段的结构合法性。

    Args:
        obj: 待检查的对象（dict / list / scalar）
        path: 当前遍历路径（用于错误提示）

    Returns:
        错误信息列表，为空表示无错误
    """
    errors: List[str] = []
    if isinstance(obj, dict):
        if "_mode" in obj:
            # 这是一个 _mode 标记值
            mode = obj.get("_mode")
            if mode not in VALID_MODES:
                errors.append(f"路径 '{path}': _mode='{mode}' 不合法，必须为 {sorted(VALID_MODES)}")
            if "value" not in obj:
                errors.append(f"路径 '{path}': _mode 标记缺少 value 字段")
            if "_label" in obj and not isinstance(obj["_label"], str):
                errors.append(f"路径 '{path}': _label 必须是字符串")
        else:
            # 普通字典，递归检查
            for key, val in obj.items():
                sub_path = f"{path}.{key}" if path else key
                errors.extend(_check_mode_structure(val, sub_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            sub_path = f"{path}[{i}]"
            errors.extend(_check_mode_structure(item, sub_path))
    return errors


def validate_generator_output(cases: List[dict]) -> List[dict]:
    """校验 LLM 生成的带 _mode 标记的测试用例列表。

    检查内容：
    1. 顶层字段类型（同 validate_llm_output）
    2. _mode 标记结构合法性
    3. 递归 body/params/headers 中的 _mode 字段

    Args:
        cases: LLM 返回的原始 JSON 解析后的 list[dict]

    Returns:
        校验通过的 list[dict]

    Raises:
        ValueError: 包含具体字段错误信息
    """
    if not isinstance(cases, list):
        raise ValueError(f"LLM 输出必须是数组，实际得到 {type(cases).__name__}")

    # Step 1: 基础字段校验（兼容 validate_llm_output 的逻辑）
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"第 {i} 条用例不是对象: {case}")

        if not case.get("name"):
            raise ValueError(f"第 {i} 条用例: name 不能为空")
        method = case.get("method", "").upper()
        if method not in VALID_HTTP_METHODS:
            raise ValueError(f"第 {i} 条用例 ('{case.get('name')}'): method '{method}' 不合法")
        if not case.get("url"):
            raise ValueError(f"第 {i} 条用例 ('{case.get('name')}'): url 不能为空")
        auth = case.get("auth", {})
        if not isinstance(auth, dict) or "type" not in auth:
            raise ValueError(f"第 {i} 条用例 ('{case.get('name')}'): auth 必须有 type 字段")

        # 确保 headers/params/body 是 dict
        for field_name in ("headers", "params", "body"):
            val = case.get(field_name)
            if val is not None and not isinstance(val, dict):
                raise ValueError(
                    f"第 {i} 条用例 ('{case.get('name')}'): {field_name} 必须是对象"
                )

        # Step 2: 检查 _case_type
        case_type = case.get("_case_type", "normal")
        if case_type not in ("normal", "error"):
            raise ValueError(
                f"第 {i} 条用例 ('{case.get('name')}'): _case_type='{case_type}' 不合法，必须为 normal 或 error"
            )

        # Step 3: 递归检查 body/params/headers 中的 _mode 标记
        for field_name in ("body", "params", "headers"):
            val = case.get(field_name, {})
            if isinstance(val, dict):
                mode_errors = _check_mode_structure(val, f"[{i}].{field_name}")
                if mode_errors:
                    raise ValueError("; ".join(mode_errors))

    logger.info("validate_generator_output: %d cases passed validation", len(cases))
    return cases


def frontend_to_db(cases: List[dict]) -> List[dict]:
    """将前端提交的带 _mode 标记的用例数据转换为 ApiRequest 可存储的格式。

    转换规则：
    - {"_mode": "user_input", "value": "admin"} -> "admin"
    - {"_mode": "ai_generated", "value": 1} -> 1
    - {"_mode": "ask_user", "value": null} -> ""（空字符串）
    - 非 _mode 标记的值原样保留

    Args:
        cases: 前端提交的用例列表，可能包含 _mode 标记

    Returns:
        转换后的用例列表，可直接用于 ApiRequest.objects.create()
    """
    result: List[dict] = []
    for case in cases:
        clean = _convert_case(case)
        # 移除前端的辅助标记字段，只保留 ApiRequest 模型字段
        clean.pop("_case_type", None)
        # 对 headers/assertions 递归清理 null 值（body 保留 null 以展示请求结构）
        for field in ("headers", "assertions"):
            if field in clean:
                clean[field] = _clean_nulls(clean[field])
        result.append(clean)

    logger.info("frontend_to_db: converted %d cases", len(result))
    return result


def _convert_value(value: Any) -> Any:
    """递归转换单个值：_mode 标记 → 实际值。"""
    if isinstance(value, dict) and "_mode" in value:
        mode = value["_mode"]
        # 使用 .get() 安全取值，缺失 value 键时返回 None
        raw_val = value.get("value")

        if mode in ("user_input", "ai_generated"):
            # user_input/ai_generated 直接取 value（可能为 None）
            return raw_val
        elif mode == "ask_user":
            # ask_user: 用户如果填了值用用户的值，否则返回空字符串
            return raw_val if raw_val is not None else ""
        else:
            # 未知 mode，原样返回 value
            return raw_val

    if isinstance(value, dict):
        return {k: _convert_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_convert_value(item) for item in value]

    return value


def _clean_nulls(data: Any) -> Any:
    """递归移除 dict 中值为 None 的键值对。

    对 dict: 移除值为 None 的条目，并对剩余值递归清理。
    对 list: 对每个元素递归清理（但不移除 None 元素本身，list 语义保留）。
    对 scalar: 原样返回。

    Args:
        data: 待清理的数据

    Returns:
        清理后的数据
    """
    if isinstance(data, dict):
        return {k: _clean_nulls(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_clean_nulls(item) for item in data]
    return data


def _convert_case(case: dict) -> dict:
    """递归转换单条用例中的所有 _mode 标记。"""
    converted: Dict[str, Any] = {}
    for key, value in case.items():
        if key.startswith("_") and key != "_case_type":
            # 跳过内部标记字段
            continue
        converted[key] = _convert_value(value)
    return converted
