"""核查 Agent：证据补全与初核（PRD 6.4 FR-POST-06）。

核查是**机器先做**的一道初核（[22] 北京银行 1 分钟出报告的思路）：
把预警当时的证据与当前可取到的材料对一遍，给出结论与是否建议升级。

三种结论：

    证据支持 CONFIRMED     证据齐备，且当前取值与命中快照**一致**
    证据不支持 UNCONFIRMED 证据齐备，但当前取值与快照**已不一致**——当初的证据站不住了
    证据不足 INSUFFICIENT   关键字段取不到——需补件

「没核查」也是一个**显式状态 + 原因码**（FR-POST-06 验收标准）：
每条预警要么有核查记录，要么有明确的"未核查"及原因，
不能是"查不到记录"这种沉默——那种沉默在统计上等于把问题藏起来。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..domain.post import (
    Alert,
    AlertLevel,
    UnverifiedReason,
    Verification,
    VerificationOutcome,
)

__all__ = ["VerifierAgent"]


class VerifierAgent:
    """核查 Agent。"""

    def __init__(self, *, clock: Optional[Any] = None) -> None:
        self._clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))

    # ---------------- 初核 ----------------
    def verify(
        self,
        alert: Alert,
        current_facts: Mapping[str, Any],
    ) -> Verification:
        """对一条预警做证据补全与初核。"""
        snapshot_facts = alert.evidence.get("facts", {}) or {}
        supplemented: List[str] = []
        gaps: List[str] = []
        changed: List[str] = []

        for name in sorted(snapshot_facts):
            snap = snapshot_facts[name] or {}
            if name not in current_facts:
                gaps.append(name)
                continue
            supplemented.append(name)
            snap_value = snap.get("value") if isinstance(snap, Mapping) else snap
            cur = current_facts[name]
            cur_value = cur.get("value") if isinstance(cur, Mapping) else cur
            if not self._same(snap_value, cur_value):
                changed.append(name)

        if gaps:
            outcome = VerificationOutcome.INSUFFICIENT
            recommend = True
            rationale = "证据不足：取不到 %s，需补件后再核" % "、".join(gaps)
        elif changed:
            outcome = VerificationOutcome.UNCONFIRMED
            recommend = alert.level is not AlertLevel.LOW
            rationale = "证据不支持：%s 的当前取值与命中快照已不一致" % "、".join(changed)
        else:
            outcome = VerificationOutcome.CONFIRMED
            recommend = False
            rationale = "证据支持：%d 项字段取值与命中快照一致" % len(supplemented)

        return Verification(
            alert_id=alert.alert_id,
            outcome=outcome,
            checked_at=self._clock(),
            checked_by="核查 Agent",
            rationale=rationale,
            evidence_gaps=tuple(gaps),
            recommend_escalation=recommend,
            supplemented_fields=tuple(supplemented),
        )

    # ---------------- 未核查 ----------------
    def mark_unverified(
        self,
        alert: Alert,
        reason_code: str,
        detail: str,
    ) -> UnverifiedReason:
        """登记"未核查"及原因（FR-POST-06：状态与原因都不得沉默）。"""
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise ValueError("未核查必须给原因码")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("未核查必须给原因说明")
        return UnverifiedReason(
            alert_id=alert.alert_id,
            reason_code=reason_code,
            detail=detail,
            at=self._clock(),
        )

    @staticmethod
    def _same(a: Any, b: Any) -> bool:
        """严格一致（与事实层同一口径：布尔与数字不相等）。"""
        if isinstance(a, bool) != isinstance(b, bool):
            return False
        return a == b