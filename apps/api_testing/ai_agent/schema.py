"""LLM 输出 Schema 强制校验层"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

VALID_HTTP_METHODS = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
})


class ApiRequestSchema(BaseModel):
    """LLM 生成的单条 API 请求，字段与 ApiRequest model 保持一致"""
    name: str = Field(..., max_length=200, description="请求名称")
    description: str = Field("", description="请求描述")
    method: str = Field("GET", description="请求方法")
    url: str = Field(..., description="请求 URL")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    params: Dict[str, str] = Field(default_factory=dict, description="URL 查询参数")
    body: Dict[str, Any] = Field(default_factory=dict, description="请求体")
    auth: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "none"}, description="认证信息"
    )
    assertions: List[Dict[str, Any]] = Field(default_factory=list, description="断言列表")
    pre_request_script: str = Field("", description="请求前脚本")
    post_request_script: str = Field("", description="请求后脚本")


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
        # 提取第一条字段错误转为 ValueError
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
