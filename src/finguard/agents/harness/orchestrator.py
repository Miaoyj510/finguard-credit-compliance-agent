"""Harness 编排器（PRD 8.2 编排拓扑、8.5 状态清单、8.7 闸门表）。

编排器是**唯一**能推动案件前进的角色。它自己**不能签核**——签核只能由
有资格的自然人通过 ``sign_gate()`` 交互完成。

编排器在本切片里守住的核心不变量：

    1. 状态迁移只能走状态机允许的边（states.TRANSITIONS）。
    2. 闸门的出边只能由签核 / 退回驱动；编排器**不能自动穿越闸门**。
    3. 进入闸门放行后继状态时，必须拿到**完整签核证据**
       （实名账号 + 时间戳 + 设备指纹 + IP），否则一律 GateBypassError。
    4. 超时**只升级、不放行**（FR-GATE-04）。
    5. 每次状态迁移与闸门动作都写一条事件（切片 6 接不可篡改哈希链）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ...domain.case_state import CaseFile, STAGE_WRITE_TOKEN
from .audit import DecisionChainAuditLog
from .gates import Amendment, GateRecord, GateState, Signer, gate_id_for
from .handoff import Handoff
from .states import (
    GATE_ENTRY,
    GATE_RELEASE,
    MAIN_PATH,
    GateBypassError,
    GateSignError,
    HandoffIncompleteError,
    Stage,
    TransitionError,
    allowed_next,
    gate_of_release,
    is_gate,
)

__all__ = ["Harness"]


class Harness:
    """一个案件的编排运行时。"""

    def __init__(
        self,
        case: CaseFile,
        *,
        event_sink: Optional[Callable[[Mapping[str, Any]], None]] = None,
        clock: Optional[Callable[[], str]] = None,
        decision_audit: Optional[DecisionChainAuditLog] = None,
    ) -> None:
        self.case = case
        self._event_sink = event_sink
        self._clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._events: List[Dict[str, Any]] = []
        self._seq = 0
        self._handoff_seq = 0
        self.decision_audit = decision_audit or DecisionChainAuditLog(clock=self._clock)
        self._counters: Dict[str, int] = {
            "bypass_attempts": 0,     # 试图绕过闸门的次数（全部被拦）
            "bypass_blocked": 0,      # 其中被成功拦下的次数
            "bypass_succeeded": 0,    # 成功绕过的次数——**恒为 0**
            "gate_releases": 0,       # 带签核证据的合法放行次数
            "gate_escalations": 0,    # 超时升级次数
            "illegal_transitions": 0, # 非法状态迁移尝试次数
            "sign_rejections": 0,     # 签核被拒次数（SEC-04：被拒次数须完整记录）
            "return_rejections": 0,   # 退回被拒次数（同为闸门动作，同口径留痕）
        }

    # ---------------- 只读 ----------------
    @property
    def stage(self) -> Stage:
        return self.case.stage

    @property
    def events(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(self._events)

    def security_counters(self) -> Dict[str, int]:
        """安全计数器（PRD 12.2 SEC-03 闸门绕行 / SEC-04 代签尝试）。"""
        return dict(self._counters)

    # ---------------- 内部 ----------------
    def _now(self) -> str:
        return self._clock()

    def _log_event(self, kind: str, **detail: Any) -> Dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "at": self._now(),
            "kind": kind,
            "case_id": self.case.case_id,
            "stage": self.case.stage.value,
            "detail": detail,
        }
        self._events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
        return event

    # ---------------- 状态迁移 ----------------
    def transition(self, to_stage: Any, *, actor: str, note: str = "") -> Stage:
        """推进案件阶段。

        四道检查，顺序固定：
            1. 放行后继状态必须有**完整签核证据**（否则 GateBypassError）
            2. 当前若在闸门上，出边只能由签核 / 退回驱动
            3. 目标必须在状态机允许的下一状态里
            4. 进入闸门 = 打开闸门（置 PENDING 并起算超时）
        """
        target = to_stage if isinstance(to_stage, Stage) else Stage(to_stage)
        current = self.case.stage

        # 1) 闸门放行校验（SEC-03）
        gate = gate_of_release(target)
        if gate is not None:
            record = self.case.gates[gate_id_for(gate)]
            if record.approval_evidence() is None:
                self._counters["bypass_attempts"] += 1
                self._counters["bypass_blocked"] += 1
                self._counters["illegal_transitions"] += 1
                self._log_event(
                    kind="gate_bypass_blocked",
                    actor=actor,
                    target=target.value,
                    gate_id=gate.value,
                    gate_state=record.state.value,
                    note="无签核证据不得进入放行后继状态",
                )
                raise GateBypassError(
                    "闸门 %s 未签核（当前 %s），不得进入 %s"
                    % (gate.value, record.state.value, target.value)
                )

        # 2) 闸门节点的出边只能由签核 / 退回驱动
        if is_gate(current):
            record = self.case.gates[gate_id_for(current)]
            released = record.state is GateState.APPROVED and target is GATE_RELEASE[current]
            returned = record.state is GateState.RETURNED and target is GATE_ENTRY[current]
            if not (released or returned):
                self._counters["bypass_attempts"] += 1
                self._counters["bypass_blocked"] += 1
                self._counters["illegal_transitions"] += 1
                self._log_event(
                    kind="gate_exit_blocked",
                    actor=actor,
                    target=target.value,
                    gate_id=current.value,
                    gate_state=record.state.value,
                    note="闸门出边只能由签核或退回驱动",
                )
                raise TransitionError(
                    "闸门 %s 的流转只能由签核或退回驱动，不得直接迁移到 %s"
                    % (current.value, target.value)
                )

        # 3) 状态机白名单
        if target not in allowed_next(current):
            self._counters["illegal_transitions"] += 1
            self._log_event(
                kind="illegal_transition",
                actor=actor,
                from_stage=current.value,
                target=target.value,
                note=note,
            )
            raise TransitionError(
                "非法状态迁移 %s -> %s（状态机无此边）" % (current.value, target.value)
            )

        # 4) 进入闸门 = 打开闸门
        if is_gate(target):
            self.case.gates[gate_id_for(target)].open(self._now())

        self.case._set_stage(target, STAGE_WRITE_TOKEN)
        self._log_event(
            kind="stage_transition",
            actor=actor,
            from_stage=current.value,
            to_stage=target.value,
            note=note,
        )
        if gate is not None:
            self._counters["gate_releases"] += 1
        return target

    # ---------------- 闸门动作 ----------------
    def sign_gate(self, gate_id: str, *, signer: Signer, note: str = "") -> GateRecord:
        """签核放行。**只有这里能产生 APPROVED**。

        被拒的签核尝试同样要留事件（PRD SEC-04「被拒次数须完整记录」、
        FR-GATE-02「拦截日志可查」）——否则审计上「没发生」与「被拦下」无法区分。
        """
        record = self.case.gates.get(gate_id)
        if record is None:
            raise GateSignError("未知闸门：%s" % gate_id)
        if record.stage is not self.case.stage:
            raise GateSignError(
                "案件当前停在 %s，不能签核 %s" % (self.case.stage.value, gate_id)
            )
        now = self._now()
        try:
            record.sign(signer, now, case_type=self.case.case_type)
        except GateSignError as exc:
            self._counters["sign_rejections"] += 1
            self._log_event(
                kind="gate_sign_rejected",
                actor=getattr(signer, "account_id", None) or "匿名账号",
                gate_id=gate_id,
                role=getattr(signer, "role", None),
                reason=str(exc),
                at=now,
                note=note,
            )
            raise
        self._log_event(
            kind="gate_signed",
            actor=signer.account_id,
            gate_id=gate_id,
            role=signer.role,
            at=now,
            note=note,
        )
        return record

    def return_gate(self, gate_id: str, *, signer: Signer, reason: str, note: str = "") -> GateRecord:
        """退回修改。理由必填；退回人与签核人过同样的资格校验（见 gates.py）。"""
        record = self.case.gates.get(gate_id)
        if record is None:
            raise GateSignError("未知闸门：%s" % gate_id)
        if record.stage is not self.case.stage:
            raise GateSignError(
                "案件当前停在 %s，不能退回 %s" % (self.case.stage.value, gate_id)
            )
        now = self._now()
        try:
            record.return_to_author(signer, reason, now, case_type=self.case.case_type)
        except GateSignError as exc:
            self._counters["return_rejections"] += 1
            self._log_event(
                kind="gate_return_rejected",
                actor=getattr(signer, "account_id", None) or "匿名账号",
                gate_id=gate_id,
                role=getattr(signer, "role", None),
                reason=str(exc),
                at=now,
                note=note,
            )
            raise
        self._log_event(
            kind="gate_returned",
            actor=signer.account_id,
            gate_id=gate_id,
            reason=reason,
            at=now,
            note=note,
        )
        return record

    def escalate_overdue(self, now: Optional[str] = None) -> Tuple[Any, ...]:
        """超时检查：**只升级、不放行**（FR-GATE-04 / G-4）。"""
        moment = now or self._now()
        out: List[Any] = []
        for gate_id, record in self.case.gates.items():
            escalation = record.check_timeout(moment)
            if escalation is None:
                continue
            self._counters["gate_escalations"] += 1
            self._log_event(
                kind="gate_escalated",
                actor="系统",
                gate_id=gate_id,
                level=escalation.level,
                to_role=escalation.to_role,
                at=moment,
                note="超时升级，闸门保持 PENDING，不自动放行",
            )
            out.append(escalation)
        return tuple(out)

    def record_amendment(
        self,
        gate_id: str,
        *,
        rule_id: str,
        rule_version: str,
        original_rationale: str,
        amended_rationale: str,
        reason: str,
        amended_by: str,
    ) -> Amendment:
        """登记人工改写（FR-GATE-06，原始意见与修改后意见双版本）。"""
        record = self.case.gates.get(gate_id)
        if record is None:
            raise GateSignError("未知闸门：%s" % gate_id)
        amendment = record.record_amendment(
            rule_id=rule_id,
            rule_version=rule_version,
            original_rationale=original_rationale,
            amended_rationale=amended_rationale,
            reason=reason,
            amended_by=amended_by,
            amended_at=self._now(),
        )
        self._log_event(
            kind="opinion_amended",
            actor=amended_by,
            gate_id=gate_id,
            rule_id=rule_id,
            rule_version=rule_version,
        )
        return amendment

    # ---------------- 留痕登记（切片 6） ----------------
    def record_opinion(self, opinion: Any) -> None:
        """登记一条审查意见（只写事件，不改状态机）。

        意见的**进出由闸门签核决定**，这里只负责留痕：
        PRD 11.3 要求「Agent 调用」的提示词版本、输出摘要、引用校验结果进日志，
        切片 6 的回放要靠这些事件重建当时的意见清单。
        """
        self._log_event(
            kind="opinion_generated",
            actor="审查 Agent",
            opinion_id=opinion.opinion_id,
            rule_id=opinion.rule_id,
            rule_version=opinion.rule_version,
            severity=getattr(opinion.severity, "value", str(opinion.severity)),
        )

    def record_disposition(self, disposition: str, **detail: Any) -> None:
        """登记风险处置决定（L6 处置与贷后闭环层）。"""
        self._log_event(
            kind="disposition",
            actor="风险研判 Agent",
            disposition=disposition,
            **detail,
        )

    # ---------------- 自动推进（遇闸门必停） ----------------
    def auto_advance(self, *, actor: str = "编排器") -> Stage:
        """沿主干路径自动推进，**遇到闸门立刻停下**。

        这是"编排器不能自动穿越闸门"的直接演示：调用到底，
        返回的一定是某个闸门状态（或终点），绝不会越过。

        两条评审修复（R1/R2）：
        · 案件**已经停在闸门**时直接原样返回——例行轮询不得变成一次
          冲向放行后继状态的尝试（那会把 SEC-03 的绕过计数灌进噪声，
          还会向调用方抛 GateBypassError，破坏"遇闸必停"的契约）。
        · 目标闸门为 RETURNED 时要**真的重新进闸**（open 会回到 PENDING），
          而不是静默不动却谎报已到达闸门。
        """
        current = self.case.stage
        if is_gate(current):
            return current
        path = MAIN_PATH
        try:
            index = path.index(current)
        except ValueError:
            return current
        for nxt in path[index + 1:]:
            if is_gate(nxt):
                self.transition(
                    nxt,
                    actor=actor,
                    note="自动推进至闸门，等待人工签核（编排器不穿越）",
                )
                return nxt
            self.transition(nxt, actor=actor, note="自动推进")
        return self.case.stage

    # ---------------- 交接 ----------------
    def build_handoff(
        self,
        *,
        from_agent: str,
        to_agent: str,
        citation_bundle: Tuple[Mapping[str, Any], ...] = (),
        open_questions: Tuple[Mapping[str, Any], ...] = (),
        producer_spec_version: str = "harness-1.0",
    ) -> Handoff:
        """按当前案件状态组装一次 Handoff，并**当场校验**。

        缺必填字段即抛 HandoffIncompleteError——交接中断，
        不允许用默认值补齐（PRD 8.6）。
        """
        coverage = self.case.coverage
        if coverage is None:
            # PRD 8.6：缺必填字段即中断，不得用零值伪造一份「已判定 0 条」的覆盖度
            raise HandoffIncompleteError(
                "案件尚未产出覆盖度统计（coverage=None），不得默认值补齐后交接"
            )
        coverage_summary = {
            "judged": coverage.judged,
            "not_applicable": coverage.not_applicable,
            "unresolved": coverage.unresolved,
            "total": coverage.total,
        }
        # human_gate_state 报的是**当前**闸门（PRD 8.6）：案件停在哪个闸门就报哪个；
        # 不在闸门上时报**最靠后**一个已触达的闸门——取最先触达的会让 GATE-A 的
        # APPROVED 记录一路冒充 GATE-B/C 的当前状态，下游误读为已签核（评审 R3）。
        current_gate = next(
            (r for r in self.case.gates.values() if r.stage is self.case.stage), None
        )
        deepest = next(
            (r for r in reversed(list(self.case.gates.values()))
             if r.state is not GateState.NOT_REACHED),
            None,
        )
        reported = current_gate if current_gate is not None else deepest
        if reported is not None:
            gate_state = {
                "gate_id": reported.gate_id,
                "state": reported.state.value,
                "has_signature": reported.approval_evidence() is not None,
            }
        else:
            gate_state = {"gate_id": self.case.stage.value, "state": "NOT_REACHED",
                          "has_signature": False}
        self._handoff_seq += 1
        # PRD 8.6：rule_binding 每项含 rule_id + version；**含 unresolved 须标原因码**。
        # 未判定条目也要进交接——下游若看不到「哪几条规则等人裁决」，
        # F-01 的入待定池语义就断在交接这一步（评审 R4；切片 1 意见 11 的对齐项）。
        rule_binding_entries = [
            {
                "rule_id": b.rule_id,
                "version": b.version,
                "binding_basis": b.binding_basis,
                "unresolved_reason_code": None,
            }
            for b in self.case.rule_binding
        ]
        for u in self.case.unresolved:
            reason_code = getattr(u, "reason_code", None)
            rule_binding_entries.append(
                {
                    "rule_id": u.rule_id,
                    "version": None,
                    "binding_basis": "须人工判定",
                    "unresolved_reason_code": (
                        reason_code.value if hasattr(reason_code, "value") else reason_code
                    ),
                }
            )
        handoff = Handoff(
            handoff_id="HO-%s-%03d" % (self.case.case_id, self._handoff_seq),
            case_id=self.case.case_id,
            from_agent=from_agent,
            to_agent=to_agent,
            stage=self.case.stage,
            rule_binding=tuple(rule_binding_entries),
            facts_digest=tuple(dict(f) for f in self.case.facts_digest),
            citation_bundle=tuple(dict(c) for c in citation_bundle),
            open_questions=tuple(dict(q) for q in open_questions),
            coverage_summary=coverage_summary,
            human_gate_state=gate_state,
            produced_at=self._now(),
            producer_spec_version=producer_spec_version,
            severity_source="rule_store",
        )
        handoff.validate()
        return handoff

    # ---------------- 测量点 ----------------
    def attach_decision_audit(self, factset: Any, *, caller: str) -> Any:
        """给事实集挂上判定链审计钩子（SEC-01 测量点）。"""
        return self.decision_audit.attach(factset, caller=caller, case_id=self.case.case_id)