"""交叉一致性核对。

PRD 6.2 FR-FACT-04 / FR-FACT-05：
    · 流水合计 vs 营业收入——偏离超阈值即产出冲突项并标注偏离幅度
    · 授权额度 vs 实际审批人授权——越权即产出冲突项

两条核对都只使用**高置信**事实（低置信字段由双路冲突裁决流程处理，
不重复在这里报冲突）。事实缺失时跳过本项——缺失由判定引擎按
MISSING_FACT 降级处理，不该在这里造一个「冲突」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "FIELD_DECLARED_REVENUE",
    "FIELD_FLOW_6M_TOTAL",
    "FIELD_APPROVAL_LIMIT",
    "FIELD_APPROVER_AUTHORITY",
    "FIELD_LOAN_AMOUNT",
    "CrossCheckResult",
    "CrossChecker",
]

FIELD_DECLARED_REVENUE = "declared_revenue"        # 申报月营收
FIELD_FLOW_6M_TOTAL = "flow_6m_total"             # 近 6 个月账户流水合计
FIELD_APPROVAL_LIMIT = "approval_limit"            # 该笔业务的授权额度
FIELD_APPROVER_AUTHORITY = "approver_authority"    # 实际审批人的授权上限
FIELD_LOAN_AMOUNT = "loan_amount"                  # 申请金额（仅作上下文展示）


@dataclass(frozen=True)
class CrossCheckResult:
    """一次核对的结果。"""

    check_id: str
    name: str
    passed: bool
    detail: str
    involved_fields: Tuple[str, ...] = ()
    deviation: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "involved_fields": list(self.involved_fields),
            "deviation": self.deviation,
        }


class CrossChecker:
    """两组交叉核对。

    deviation_threshold 是**自建口径 [D]**（PRD 3.4 假设），W3 用真实样本回填。
    """

    def __init__(self, deviation_threshold: float = 0.20) -> None:
        if not 0 < deviation_threshold < 1:
            raise ValueError("偏离阈值必须落在 (0, 1) 之间")
        self.deviation_threshold = deviation_threshold

    # ---------- 核对一：流水合计 vs 申报营收 ----------
    def check_revenue_vs_flow(self, facts: Mapping[str, Any]) -> Optional[CrossCheckResult]:
        declared = facts.get(FIELD_DECLARED_REVENUE)
        flow = facts.get(FIELD_FLOW_6M_TOTAL)
        if not isinstance(declared, (int, float)) or isinstance(declared, bool):
            return None
        if not isinstance(flow, (int, float)) or isinstance(flow, bool):
            return None
        if declared == 0:
            return None
        deviation = (declared - flow) / declared
        passed = abs(deviation) <= self.deviation_threshold
        return CrossCheckResult(
            check_id="CC-01",
            name="申报营收 vs 账户流水合计",
            passed=passed,
            detail=(
                "申报营收 %s，流水合计 %s，偏离 %.1f%%（阈值 %.0f%%）"
                % (declared, flow, deviation * 100, self.deviation_threshold * 100)
            ),
            involved_fields=(FIELD_DECLARED_REVENUE, FIELD_FLOW_6M_TOTAL),
            deviation=deviation,
        )

    # ---------- 核对二：授权额度 vs 实际审批人授权 ----------
    def check_approval_authority(self, facts: Mapping[str, Any]) -> Optional[CrossCheckResult]:
        limit = facts.get(FIELD_APPROVAL_LIMIT)
        authority = facts.get(FIELD_APPROVER_AUTHORITY)
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            return None
        if not isinstance(authority, (int, float)) or isinstance(authority, bool):
            return None
        passed = authority >= limit
        amount = facts.get(FIELD_LOAN_AMOUNT)
        ctx = ""
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            ctx = "；申请金额 %s" % (amount,)
        return CrossCheckResult(
            check_id="CC-02",
            name="授权额度 vs 实际审批人授权",
            passed=passed,
            detail=(
                "该笔业务授权额度 %s，实际审批人授权上限 %s%s"
                % (limit, authority, ctx)
            ),
            involved_fields=(FIELD_APPROVAL_LIMIT, FIELD_APPROVER_AUTHORITY),
            deviation=None,
        )

    # ---------- 汇总 ----------
    def run(self, facts: Mapping[str, Any]) -> Tuple[CrossCheckResult, ...]:
        """跑全部核对。事实缺失的项直接跳过，不产生冲突。"""
        results = []
        for check in (self.check_revenue_vs_flow, self.check_approval_authority):
            result = check(facts)
            if result is not None:
                results.append(result)
        return tuple(results)
