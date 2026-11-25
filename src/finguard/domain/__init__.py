"""领域模型包。"""
from .case import CaseState, TripleKey
from .enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from .rule import Rule

__all__ = [
    "CaseState",
    "TripleKey",
    "CoverageFlag",
    "Severity",
    "UnresolvedReasonCode",
    "Verdict",
    "Rule",
]
