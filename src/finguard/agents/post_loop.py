"""贷后闭环编排：监测 → 预警 → 核查 → GATE-C 处置 → 归档。

**为什么贷后段不���用 auto_advance()**：切片 5 的 ``auto_advance()`` 只沿主干路径
（MAIN_PATH）推进，分支态（S1_MANUAL_CONFLICT / S2_UNRESOLVED）与贷后业务分支
不在其中（切片 5 评审留档 A1）。本模块因此**全部走显式 ``transition()``**——
每一步推进都对应一个明确的业务动作，而不是"沿着路标一路按下去"。

处置四档（FR-POST-05）与人审闸门（FR-GATE-01/03/04）在这里合成一条链：

    S8 核查初核 → GATE-C（开闸，置 PENDING）→ **人工签核** → S9 处置归档 → S10

**处置必须发生在签核之后**：没签核就想落处置，闸门会把流程挡在 GATE_C。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.post import (
    Alert,
    PostCaseView,
    PostDisposition,
    UnverifiedReason,
    Verification,
)
from .harness.gates import Signer
from .harness.orchestrator import Harness
from .harness.states import GateBypassError, Stage
from .post_monitor import PostMonitorAgent
from .verifier import VerifierAgent

__all__ = ["PostFlow", "POST_CASE_TYPE"]

#: 贷后监测的案件类型（PRD 8.4 CaseState.case_type 的三选一之一）
POST_CASE_TYPE = "post_monitoring"


class PostFlow:
    """贷后闭环编排。"""

    def __init__(
        self,
        harness: Harness,
        monitor: PostMonitorAgent,
        verifier: VerifierAgent,
    ) -> None:
        self.harness = harness
        self.monitor = monitor
        self.verifier = verifier
        self._verifications: List[Verification] = []
        self._unverified: List[UnverifiedReason] = []
        self._disposed: Dict[str, Alert] = {}

    # ---------------- 结果 ----------------
    @property
    def verifications(self) -> Tuple[Verification, ...]:
        return tuple(self._verifications)

    @property
    def unverified(self) -> Tuple[UnverifiedReason, ...]:
        return tuple(self._unverified)

    @property
    def disposed(self) -> Tuple[Alert, ...]:
        return tuple(self._disposed.values())

    # ---------------- 流程 ----------------
    def enter_monitoring(self, view: PostCaseView) -> None:
        """主流程 S6 → POST_LOOP，案件转入贷后监测名单。"""
        if self.harness.stage is not Stage.S6:
            raise GateBypassError(
                "案件须先完成归档（S6）才能转入贷后监测，当前 %s" % self.harness.stage.value
            )
        self.harness.transition(Stage.POST_LOOP, actor="编排器", note="转入贷后常驻监测")
        # 贷后监测是**另一种案件类型**（PRD 8.4 的 case_type 三选一：
        # corp_credit_review / 小额经营贷 / post_monitoring）。案件号不变，
        # 但案件类型要切换——否则 GATE-C 的资格校验（FR-GATE-02 三重校验）
        # 仍会拿贷前类型去校验，贷后管理岗就签不了自己该签的预警。
        # 切换**留痕**，否则回放时还原不出当时的口径。
        previous = self.harness.case.case_type
        if previous != POST_CASE_TYPE:
            self.harness.case.case_type = POST_CASE_TYPE
            self.harness.record_note(
                "case_type_switched",
                previous=previous,
                current=POST_CASE_TYPE,
                reason="转入贷后常驻监测",
            )
        self.monitor.register(view)
        self.monitor.start()

    def scan(self, as_of: Optional[str] = None) -> Tuple[Alert, ...]:
        """POST_LOOP → S7 扫描；未命中则回到 S6（PRD 图 8-1 的回流边）。"""
        if self.harness.stage is not Stage.POST_LOOP:
            raise GateBypassError(
                "案件不在贷后循环中，当前 %s" % self.harness.stage.value
            )
        produced = self.monitor.scan_once(as_of)
        if not produced:
            self.harness.transition(Stage.S6, actor="编排器", note="扫描未命中，回到已归档")
            return ()
        self.harness.transition(Stage.S7, actor="编排器", note="扫描命中，生成预警")
        return produced

    def verify(self, alert: Alert, current_facts: Mapping[str, Any]) -> Verification:
        """S7 → S8 核查初核。"""
        if self.harness.stage is not Stage.S7:
            raise GateBypassError("预警须先生成才能核查，当前 %s" % self.harness.stage.value)
        self.harness.transition(Stage.S8, actor="核查 Agent", note="进入核查初核")
        verification = self.verifier.verify(alert, current_facts)
        self._verifications.append(verification)
        return verification

    def mark_unverified(
        self,
        alert: Alert,
        reason_code: str,
        detail: str,
    ) -> UnverifiedReason:
        """登记"未核查"及原因（FR-POST-06：状态与原因都不得沉默）。"""
        reason = self.verifier.mark_unverified(alert, reason_code, detail)
        self._unverified.append(reason)
        return reason

    def submit_for_disposition(self, alert: Alert) -> None:
        """S8 → GATE_C：提交处置签核（闸门置 PENDING，等待人工）。"""
        if self.harness.stage is not Stage.S8:
            raise GateBypassError(
                "须先完成核查初核才能提交处置，当前 %s" % self.harness.stage.value
            )
        self.harness.transition(Stage.GATE_C, actor="核查 Agent", note="提交处置签核")

    def dispose(
        self,
        alert: Alert,
        disposition: PostDisposition,
        signer: Signer,
        *,
        reason: Optional[str] = None,
        at: Optional[str] = None,
    ) -> Alert:
        """GATE-C 签核 → 处置 → S9。

        **处置必须发生在签核之后**：闸门没放行就落处置，`transition(S9)` 会被拦。
        「关闭」档必须写理由（作业卡片要求引用核查结论）。
        """
        if not isinstance(disposition, PostDisposition):
            raise ValueError("处置档位必须是四档之一：%r" % (disposition,))
        if disposition is PostDisposition.CLOSE:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("「关闭」档必须填写关闭理由（作业卡片要求引用核查结论）")
        self.harness.sign_gate("GATE-C", signer=signer, note="预警处置签核")
        moment = at or datetime.now().isoformat(timespec="seconds")
        disposed = alert.with_disposition(disposition, signer.account_id, moment)
        self._disposed[alert.alert_id] = disposed
        self.harness.record_disposition(
            disposition.value,
            alert_id=alert.alert_id,
            rule_id=alert.rule_id,
            rule_version=alert.rule_version,
            reason=reason or "",
        )
        self.harness.transition(Stage.S9, actor="编排器", note="处置已签核，归档处置结果")
        return disposed

    def archive(self) -> None:
        """S9 → S10 可回放归档。"""
        if self.harness.stage is not Stage.S9:
            raise GateBypassError("须先完成处置归档，当前 %s" % self.harness.stage.value)
        self.harness.transition(Stage.S10, actor="编排器", note="贷后闭环归档")