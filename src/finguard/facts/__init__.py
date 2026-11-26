"""事实层（PRD 6.2、8.2）。

事实层是工具层子模块，**不占 Agent 角色名额、不具备自主决策权**。
它只做四件事：取数、双路合并、交叉核对、冲突入队。
"""
from .conflict import ConflictItem, ConflictKind, ConflictQueue, ConflictStatus
from .crosscheck import CrossChecker, CrossCheckResult
from .extract import RouteResult, distinct_candidates, merge_dual
from .layer import ConclusionTermError, FactLayer
from .models import (
    CONCLUSION_TERMS,
    Confidence,
    FactField,
    FactSet,
    SourceChannel,
    scan_conclusion_terms,
)

__all__ = [
    "CONCLUSION_TERMS",
    "Confidence",
    "ConclusionTermError",
    "ConflictItem",
    "ConflictKind",
    "ConflictQueue",
    "ConflictStatus",
    "CrossCheckResult",
    "CrossChecker",
    "FactField",
    "FactLayer",
    "FactSet",
    "RouteResult",
    "SourceChannel",
    "distinct_candidates",
    "merge_dual",
    "scan_conclusion_terms",
]
