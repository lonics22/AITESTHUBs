"""从当前项目中检索可复用的测试数据上下文"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def retrieve_project_context(project_id: int) -> Dict[str, Any]:
    """
    检索项目中已有的测试数据上下文。

    Args:
        project_id: 测试项目ID（对应管理后台 Project 模型）

    Returns:
        包含可复用数据的上下文字典
    """
    context = {
        "existing_usernames": [],
        "available_ids": [],
        "related_entities": [],
        "recent_executions": [],
    }

    if not project_id:
        return context

    try:
        _load_api_requests(context)
        _load_execution_history(context)
    except Exception as e:
        logger.warning(f"Project context retrieval failed: {e}")

    return context


def _load_api_requests(context: Dict[str, Any]) -> None:
    """加载最近的 API 请求，提取可复用的数据实体"""
    try:
        from apps.api_testing.models import ApiRequest

        requests = ApiRequest.objects.all().order_by('-created_at')[:20].values(
            'name', 'method', 'url', 'body'
        )

        entities = []
        for req in requests:
            body_preview = str(req.get('body', ''))[:200] if isinstance(req.get('body'), (dict, list)) else str(req.get('body', ''))[:200]
            entities.append({
                'name': req.get('name', ''),
                'method': req.get('method', ''),
                'url': str(req.get('url', ''))[:100],
                'body_preview': body_preview,
            })

        context['related_entities'] = entities
    except Exception as e:
        logger.warning(f"Failed to load API requests for context: {e}")


def _load_execution_history(context: Dict[str, Any]) -> None:
    """加载最近的执行记录，提取成功的响应数据"""
    try:
        from apps.api_testing.models import RequestHistory

        recent = RequestHistory.objects.filter(
            status_code__gte=200,
            status_code__lt=300
        ).order_by('-executed_at')[:10].select_related('request')

        executions = []
        ids = set()
        for exec_record in recent:
            method = ''
            url = ''
            if exec_record.request:
                method = exec_record.request.method or ''
                url = exec_record.request.url or ''

            executions.append({
                'url': str(url)[:100],
                'method': method,
                'status': exec_record.status_code,
                'response': str(exec_record.response_data or '')[:300],
            })
            # 从成功响应中收集 ID 字段
            resp = exec_record.response_data
            if isinstance(resp, dict):
                for key in ('id', 'user_id', 'order_id', 'product_id', 'token'):
                    val = resp.get(key)
                    if val is not None:
                        ids.add(str(val))

        context['recent_executions'] = executions
        context['available_ids'] = sorted(ids)
    except Exception as e:
        logger.warning(f"Failed to load execution history for context: {e}")
