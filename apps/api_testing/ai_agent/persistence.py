"""LangGraph checkpoint 持久化到 AIImportTask.generated_summary 字段"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


class DjangoCheckpointSaver(BaseCheckpointSaver):
    """将 Agent 的检查点保存到 AIImportTask.generated_summary 字段中。"""

    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Dict[str, Any],
        metadata: Dict[str, Any],
        new_versions: Dict[str, Any],
    ) -> Dict[str, Any]:
        from apps.api_testing.models import AIImportTask

        task_id = config.get("configurable", {}).get("task_id")
        if not task_id:
            return config

        try:
            task = AIImportTask.objects.get(id=task_id)
            current = task.generated_summary or {}
            current.update({"agent_state": checkpoint, "agent_metadata": metadata})
            task.generated_summary = current
            task.save(update_fields=["generated_summary"])
        except AIImportTask.DoesNotExist:
            logger.warning("DjangoCheckpointSaver: task %s not found", task_id)

        return config

    def get(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from apps.api_testing.models import AIImportTask

        task_id = config.get("configurable", {}).get("task_id")
        if not task_id:
            return None

        try:
            task = AIImportTask.objects.get(id=task_id)
            summary = task.generated_summary
            if isinstance(summary, dict) and "agent_state" in summary:
                return summary["agent_state"]
        except AIImportTask.DoesNotExist:
            pass

        return None
