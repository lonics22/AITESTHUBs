"""AI Import 自定义异常"""
from __future__ import annotations


class WorkflowError(Exception):
    """工作流执行过程中的通用异常"""
    pass


class ReviewRejected(WorkflowError):
    """评审不通过，但已耗尽重试次数"""
    def __init__(self, message: str, review_score: float = 0.0):
        self.review_score = review_score
        super().__init__(message)


class RetryExhausted(WorkflowError):
    """重试次数耗尽"""
    pass
