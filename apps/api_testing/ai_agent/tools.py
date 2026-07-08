"""Agent 工具集 — 4 个 @tool 函数"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from apps.api_testing.ai_agent.schema import validate_llm_output

logger = logging.getLogger(__name__)


@tool
def parse_document_tool(raw_content: dict) -> List[dict]:
    """解析上传的 API 文档内容，提取端点列表。
    支持格式: Swagger 2.0, OpenAPI 3.0, Postman Collection, HAR。
    返回归一化的端点列表 [{path, method, summary, parameters, ...}]"""
    from apps.api_testing.doc_parser import parse_document

    endpoints = parse_document(raw_content)
    logger.info("parse_document_tool: parsed %d endpoints", len(endpoints))
    return endpoints


@tool
def classify_parameters_tool(endpoints: List[dict]) -> dict:
    """分析端点的所有参数，按 auto/manual/context_ref 分类。
    - auto: LLM 可以自动生成测试值
    - manual: 需要用户提供值
    - context_ref: 引用已有上下文"""
    from apps.api_testing.ai_import_service import analyze_endpoints

    classification = analyze_endpoints(endpoints)
    logger.info(
        "classify_parameters_tool: %d endpoints, %d auto, %d manual, %d context_ref",
        classification.get("endpoint_count", 0),
        classification.get("auto_params", 0),
        classification.get("manual_params", 0),
        classification.get("context_ref_params", 0),
    )
    return classification


@tool
def generate_test_cases_tool(
    endpoints: List[dict],
    classification: dict,
    user_answers: dict,
    tester_prompt: str,
) -> List[dict]:
    """为每个端点生成完整的 API 测试用例。
    使用 tester.md 提示词规范，输出必须符合 ApiRequest 模型字段 schema。"""
    from apps.api_testing.ai_import_service import generate_requests

    environment_vars = user_answers.get("environment_vars", {})
    answers = user_answers.get("answers", {})

    raw_requests = generate_requests(
        endpoints, classification, answers, environment_vars,
    )

    try:
        validated = validate_llm_output(raw_requests)
        logger.info("generate_test_cases_tool: %d requests validated", len(validated))
        return validated
    except ValueError as e:
        logger.warning("generate_test_cases_tool: validation failed: %s", e)
        raise


@tool
def save_to_database_tool(
    requests: List[dict],
    project_id: int,
    collection_id: Optional[int],
    auto_structure: bool,
    user_id: int,
) -> dict:
    """将生成的 API 请求保存到数据库。
    按标签自动创建 ApiCollection，或者保存到指定集合。
    返回 {collections_created: [], requests_created: []}"""
    from django.db import transaction
    from apps.api_testing.models import ApiProject, ApiCollection, ApiRequest

    with transaction.atomic():
        project = ApiProject.objects.get(id=project_id)

        created_collections: List[int] = []
        created_request_ids: List[int] = []

        if auto_structure:
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            collection, _ = ApiCollection.objects.get_or_create(
                project=project,
                name=f"AI Agent Import {now_str}",
                defaults={"description": "AI Agent 自动导入的 API 集合"},
            )
            created_collections.append(collection.id)
        else:
            if collection_id:
                collection = ApiCollection.objects.get(id=collection_id)
            else:
                from datetime import datetime
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                collection = ApiCollection.objects.create(
                    project=project,
                    name=f"AI Agent Import {now_str}",
                    description="AI Agent 自动导入的 API 集合",
                )
                created_collections.append(collection.id)

        for req_data in requests:
            api_request = ApiRequest.objects.create(
                collection=collection,
                name=str(req_data.get("name", ""))[:200],
                description=req_data.get("description", ""),
                method=req_data.get("method", "GET"),
                url=req_data.get("url", ""),
                headers=req_data.get("headers", {}),
                params=req_data.get("params", {}),
                body=req_data.get("body", {}),
                auth=req_data.get("auth", {}),
                assertions=req_data.get("assertions", []),
                pre_request_script=req_data.get("pre_request_script", ""),
                post_request_script=req_data.get("post_request_script", ""),
                created_by_id=user_id,
            )
            created_request_ids.append(api_request.id)

    logger.info(
        "save_to_database_tool: %d collections, %d requests created",
        len(created_collections), len(created_request_ids),
    )
    return {
        "collections_created": created_collections,
        "requests_created": created_request_ids,
    }
