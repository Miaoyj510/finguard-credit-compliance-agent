"""事实层编排：取数 -> 双路合并 -> 交叉核对 -> 冲突入队 -> 产出可判定事实集。

PRD 6.2 FR-FACT-01 ~ FR-FACT-07 的落地点。

三条硬约束在这里被强制执行：
    1. 事实层**不输出结论**（FR-FACT-07）：产出前扫结论词，命中即失败。
    2. 低置信与未裁决冲突**不进判定链**（FR-FACT-03）：只有 FactSet.decision_ready()
       的内容能出去。
    3. 冲突项**并列候选值**交人工裁决（FR-FACT-06），裁决必须带理由。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .conflict import ConflictItem, ConflictKind, ConflictQueue, ConflictStatus
from .crosscheck import (
    CrossChecker,
    CrossCheckResult,
    FIELD_APPROVAL_LIMIT,
    FIELD_APPROVER_AUTHORITY,
    FIELD_DECLARED_REVENUE,
    FIELD_FLOW_6M_TOTAL,
)
from .extract import RouteResult, merge_dual
from .models import Confidence, FactField, FactSet, scan_conclusion_terms

__all__ = ["ConclusionTermError", "FactLayer"]


class ConclusionTermError(ValueError):
    """事实层输出里出现了结论性词汇——越界做了判定（FR-FACT-07）。"""


@dataclass(frozen=True)
class FactLayer:
    """事实层。

    checker : 交叉核对器（可注入，便于换阈值或换实现）
    strict  : 是否在产出前强制扫描结论词。默认 True（PRD 要求「出现结论词即失败」）。
    """

    checker: CrossChecker = CrossChecker()
    strict: bool = True

    def build(self, route_a: RouteResult, route_b: RouteResult) -> Tuple[FactSet, ConflictQueue]:
        """跑完一次事实层，产出事实集 + 冲突队列。"""
        merged = merge_dual(route_a, route_b)

        # 只用高置信事实做交叉核对——低置信字段由双路冲突流程处理，不重复报冲突
        decision = {
            name: f.value for name, f in merged.items() if f.confidence is Confidence.HIGH
        }
        checks = self.checker.run(decision)

        queue = ConflictQueue()
        for name, f in merged.items():
            if f.confidence is not Confidence.LOW:
                continue
            queue.add(
                ConflictItem(
                    conflict_id="%s:%s" % (ConflictKind.LOW_CONFIDENCE.value, name),
                    field=name,
                    kind=ConflictKind.LOW_CONFIDENCE,
                    candidates=f.candidates,
                    detail=f.note,
                )
            )

        for check in checks:
            if check.passed:
                continue
            fields = check.involved_fields
            values = tuple(decision[f] for f in fields if f in decision)
            queue.add(
                ConflictItem(
                    conflict_id="%s:%s" % (ConflictKind.CROSS_CHECK.value, check.check_id),
                    field=fields[0] if fields else check.check_id,
                    kind=ConflictKind.CROSS_CHECK,
                    candidates=values,
                    detail=check.detail,
                )
            )

        factset = FactSet(fields=merged, pending_conflicts=queue.pending(), checks=checks)

        if self.strict:
            hits = factset.conclusion_hits()
            if hits:
                raise ConclusionTermError(
                    "事实层输出包含结论性词汇（PRD FR-FACT-07）：%s"
                    % "、".join("%s→%s" % (name, term) for name, term in hits)
                )

        return factset, queue

    def adjudicate(
        self,
        factset: FactSet,
        queue: ConflictQueue,
        conflict_id: str,
        chosen: Any,
        reason: str,
        adjudicator: str,
        adjudicated_at: Optional[str] = None,
    ) -> FactSet:
        """人工裁决一条冲突，返回新的事实集。"""
        queue.resolve(conflict_id, chosen, reason, adjudicator, adjudicated_at)
        fields = queue.apply_resolutions(factset.fields)
        return FactSet(
            fields=fields,
            pending_conflicts=queue.pending(),
            checks=factset.checks,
        )
