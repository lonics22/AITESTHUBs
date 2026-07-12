"""数据类定义 — ReviewReport、FeedbackItem、EnhancedEndpoint 等"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict


# ---------------------------------------------------------------------------
# Reviewer 相关数据类
# ---------------------------------------------------------------------------

@dataclass
class FeedbackItem:
    """评审反馈中的一条具体问题"""
    type: Literal[
        'missing_coverage',    # 端点缺少用例
        'missing_assertion',   # 用例缺少断言
        'data_issue',          # 数据值不合理
        'mode_misuse',         # _mode 分配不当
        'label_missing',       # user_input 缺少 _label
    ]
    severity: Literal['error', 'warning', 'info']
    endpoint: Optional[str] = None       # "GET /users/{id}"
    case_index: Optional[int] = None
    field: Optional[str] = None          # 具体字段名
    suggestion: str = ''                 # 修正建议


@dataclass
class FeedbackPayload:
    """评审反馈负载，可直接用于提示 Generator 修正"""
    items: List[FeedbackItem] = field(default_factory=list)
    summary: str = ''                           # 人类可读摘要
    retry_prompt_snippet: str = ''              # 可直接注入 Generator prompt

    def add_item(self, **kwargs) -> FeedbackItem:
        item = FeedbackItem(**kwargs)
        self.items.append(item)
        return item


@dataclass
class ReviewReport:
    """评审报告"""
    passed: bool = False
    total_score: float = 0.0
    coverage_score: float = 0.0
    assertion_score: float = 0.0
    data_score: float = 0.0
    feedback: FeedbackPayload = field(default_factory=FeedbackPayload)


# ---------------------------------------------------------------------------
# Parser 相关数据类
# ---------------------------------------------------------------------------

class EnhancedEndpoint(TypedDict, total=False):
    """增强后的端点，比原始 ParsedEndpoint 多 AI 元数据"""
    # 原始字段（保持兼容）
    path: str
    method: str
    summary: str
    description: str
    tags: List[str]
    parameters: List[Dict[str, Any]]
    request_body: Optional[Dict[str, Any]]
    responses: Dict[str, Any]
    security: List[Dict[str, Any]]
    deprecated: bool

    # 新增字段
    field_descriptions: Dict[str, str]
    complexity_score: float
    suggested_case_count: int
    has_auth: bool
    has_body: bool


# ---------------------------------------------------------------------------
# Saver 相关数据类
# ---------------------------------------------------------------------------

@dataclass
class SaveResult:
    """保存结果"""
    requests_created: int = 0
    requests_details: List[Dict[str, Any]] = field(default_factory=list)
    collections_created: int = 0
