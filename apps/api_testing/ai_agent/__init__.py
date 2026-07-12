"""AI Import Agent — 增强型工作流

组件:
- generator: AnalyzeGenerator (继承 AnalyzeGenerator 的生成逻辑)
- reviewer_agent: ReviewerAgent (质量门禁评审)
- orchestrator: WorkflowEngine (工作流编排)
- mode_strategy: 确定性 _mode 决策规则
- schema: 校验与格式转换
- memory: 记忆系统 (Phase 3)
- saver_agent: SaverAgent (Phase 3)
"""
from apps.api_testing.ai_agent.generator import AnalyzeGenerator
from apps.api_testing.ai_agent.reviewer_agent import ReviewerAgent
from apps.api_testing.ai_agent.orchestrator import WorkflowEngine
from apps.api_testing.ai_agent.mode_strategy import (
    decide_mode_strategy,
    apply_user_preference,
)
from apps.api_testing.ai_agent.schema import (
    ApiRequestSchema,
    BatchApiRequestsSchema,
    validate_llm_output,
    validate_generator_output,
    frontend_to_db,
)
from apps.api_testing.ai_agent.saver_agent import SaverAgent
from apps.api_testing.ai_agent.memory import (
    SessionMemory,
    MemoryAugmentedPromptBuilder,
    get_user_preference,
    record_user_preference,
)

__all__ = [
    # 生成
    "AnalyzeGenerator",
    # 评审
    "ReviewerAgent",
    # 编排
    "WorkflowEngine",
    # 模式决策
    "decide_mode_strategy",
    "apply_user_preference",
    # Schema / 校验
    "ApiRequestSchema",
    "BatchApiRequestsSchema",
    "validate_llm_output",
    "validate_generator_output",
    "frontend_to_db",
    # Saver (Phase 3)
    "SaverAgent",
    # 记忆系统 (Phase 3)
    "SessionMemory",
    "MemoryAugmentedPromptBuilder",
    "get_user_preference",
    "record_user_preference",
]
