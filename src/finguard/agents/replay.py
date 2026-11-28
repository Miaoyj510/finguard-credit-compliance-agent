"""案件回放（PRD 8.9 回放能力规格、8.10、F-09 / F-13、11.4 S-09）。

回放规格（PRD 8.9 原文）：

    · 粒度：案件级 + 事件级（按 seq 重放）
    · 可复现对象：当时的规则版本集合、文档依据、AgentSpec 版本、事实快照
    · 复现方式：指定 case_id + 起始 seq，重放至任意节点，输出与历史一致的结论
    · 完整性校验：六层齐全性检查；任一层缺失则标记"不完整"且不可用于监管取证
    · 不可篡改：事件日志只追加；卷宗层用内容哈希校验

本模块做三件事：

1. **按事件重建结论轨迹**（阶段序列 + 三处签核 + 意见编号）——只用事件日志，
   不读卷宗，保证"重放"是真的重算而不是复读。
2. **与卷宗对账**：重建轨迹必须与卷宗 L4 / L5 记录的轨迹一致，否则说明
   当时的结论与留痕对不上，标记不一致。
3. **给出能不能用于监管取证的结论**：链完整 + 六层齐全 + 轨迹一致，三者缺一不可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..storage.dossier import Completeness, Dossier, DossierLayer
from ..storage.event_log import Event, EventLog
from .harness.states import TRANSITIONS, bypass_edges

__all__ = ["ConclusionTrace", "ReplayResult", "ReplayEngine"]


@dataclass(frozen=True)
class ConclusionTrace:
    """重放重建出的结论轨迹。

    ``amendments`` 是人工改写留痕（FR-GATE-06 双版本）的三元组
    （gate_id, rule_id, rule_version）：事件日志里有 ``opinion_amended``
    而卷宗 L5 里对应 amendment 消失时，对账必须报不一致——
    「删除原始意见」走合法覆盖写也要能检出。
    """

    stages: Tuple[str, ...] = ()
    signatures: Tuple[Tuple[str, str, str], ...] = ()
    opinion_ids: Tuple[str, ...] = ()
    amendments: Tuple[Tuple[str, str, str], ...] = ()
    final_stage: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stages": list(self.stages),
            "signatures": [list(s) for s in self.signatures],
            "opinion_ids": list(self.opinion_ids),
            "amendments": [list(a) for a in self.amendments],
            "final_stage": self.final_stage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConclusionTrace":
        return cls(
            stages=tuple(data.get("stages", ())),
            signatures=tuple(tuple(s) for s in data.get("signatures", ())),
            opinion_ids=tuple(data.get("opinion_ids", ())),
            amendments=tuple(tuple(a) for a in data.get("amendments", ())),
            final_stage=data.get("final_stage", ""),
        )


@dataclass(frozen=True)
class ReplayResult:
    """一次回放的结果。"""

    case_id: str
    from_seq: int
    to_seq: Optional[int]
    events: Tuple[Event, ...]
    trace: ConclusionTrace
    chain_ok: bool
    chain_reason: str = ""
    completeness: Completeness = Completeness.INCOMPLETE
    missing_layers: Tuple[str, ...] = ()
    matches_dossier: bool = False
    # 重放算出的最终阶段必须与案件当前阶段一致（不一致即视同链断裂）
    stage_consistent: bool = True
    # 重放轨迹必须满足状态机拓扑：逐对相邻阶段在白名单内，且无绕行边
    topology_ok: bool = True

    @property
    def usable_for_audit(self) -> bool:
        """能否用于监管取证：链完整 + 六层齐全 + 轨迹与卷宗一致，三者缺一不可。

        评审修复（RP1/RP7）：**空窗口一律不可取证**。重放区间没有事件、
        或事件里重放不出任何结论内容（阶段/签核/意见/改写全空）时，
        「一致性」是空集合上的恒真——不存在的案件、越界的 seq 区间都会
        被误判为可取证。取证判定必须建立在**重放出了东西**的前提上。
        """
        if not self.events:
            return False
        trace = self.trace
        if not (trace.stages or trace.signatures or trace.opinion_ids or trace.amendments):
            return False
        return (
            self.chain_ok
            and self.completeness is Completeness.COMPLETE
            and self.matches_dossier
            and self.stage_consistent
            and self.topology_ok
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "from_seq": self.from_seq,
            "to_seq": self.to_seq,
            "event_count": len(self.events),
            "trace": self.trace.to_dict(),
            "chain_ok": self.chain_ok,
            "chain_reason": self.chain_reason,
            "completeness": self.completeness.value,
            "missing_layers": list(self.missing_layers),
            "matches_dossier": self.matches_dossier,
            "stage_consistent": self.stage_consistent,
            "topology_ok": self.topology_ok,
            "usable_for_audit": self.usable_for_audit,
        }


class ReplayEngine:
    """回放引擎。"""

    def __init__(self, event_log: EventLog, dossier: Dossier) -> None:
        self.log = event_log
        self.dossier = dossier

    # ---------------- 轨迹重建 ----------------
    @staticmethod
    def rebuild_trace(events: Sequence[Event]) -> ConclusionTrace:
        """**只**从事件重建结论轨迹。"""
        stages: List[str] = []
        signatures: List[Tuple[str, str, str]] = []
        opinions: List[str] = []
        amendments: List[Tuple[str, str, str]] = []
        for event in events:
            payload = event.payload or {}
            kind = event.kind
            if kind == "stage_transition":
                to_stage = payload.get("to_stage")
                if to_stage:
                    stages.append(to_stage)
            elif kind == "gate_signed":
                gate_id = payload.get("gate_id")
                if gate_id:
                    signatures.append(
                        (
                            gate_id,
                            payload.get("actor") or "",
                            payload.get("at") or event.at,
                        )
                    )
            elif kind == "opinion_generated":
                opinion_id = payload.get("opinion_id")
                if opinion_id:
                    opinions.append(opinion_id)
            elif kind == "opinion_amended":
                gate_id = payload.get("gate_id")
                if gate_id:
                    amendments.append(
                        (
                            gate_id,
                            payload.get("rule_id") or "",
                            payload.get("rule_version") or "",
                        )
                    )
        return ConclusionTrace(
            stages=tuple(stages),
            signatures=tuple(signatures),
            opinion_ids=tuple(opinions),
            amendments=tuple(sorted(amendments)),
            final_stage=stages[-1] if stages else "",
        )

    @staticmethod
    def dossier_trace(dossier: Dossier) -> ConclusionTrace:
        """从卷宗读出当时记录的轨迹（L4 意见 + L5 签核、阶段轨迹与改写留痕）。"""
        stages: Tuple[str, ...] = ()
        signatures: List[Tuple[str, str, str]] = []
        opinions: Tuple[str, ...] = ()
        amendments: List[Tuple[str, str, str]] = []
        l5 = dossier.read(DossierLayer.L5)
        if l5 is not None:
            stages = tuple(l5.payload.get("stage_trail", ()))
            gates = l5.payload.get("gates", {}) or {}
            for gate_id in sorted(gates):
                gate = gates[gate_id] or {}
                if gate.get("signed_by"):
                    signatures.append(
                        (gate_id, gate.get("signed_by"), gate.get("signed_at") or "")
                    )
                for a in gate.get("amendments", ()) or ():
                    amendments.append(
                        (
                            gate_id,
                            (a or {}).get("rule_id") or "",
                            (a or {}).get("rule_version") or "",
                        )
                    )
        l4 = dossier.read(DossierLayer.L4)
        if l4 is not None:
            opinions = tuple(
                o.get("opinion_id") for o in (l4.payload.get("opinions", []) or [])
                if o.get("opinion_id")
            )
        return ConclusionTrace(
            stages=stages,
            signatures=tuple(signatures),
            opinion_ids=opinions,
            amendments=tuple(sorted(amendments)),
            final_stage=stages[-1] if stages else "",
        )

    # ---------------- 回放 ----------------
    def replay(
        self,
        case_id: str,
        from_seq: int = 1,
        to_seq: Optional[int] = None,
        expected_stage: Optional[str] = None,
    ) -> ReplayResult:
        """按 seq 重放指定案件，返回结论轨迹与可取证性判定。

        对账口径（评审修复 RP2）：**始终用案件全部事件重建完整轨迹与卷宗比对**，
        重放窗口只决定「这次输出哪一段轨迹」。此前按「窗口轨迹 vs 卷宗轨迹」做
        后缀比对，导致 PRD 8.9「重放至任意节点」的前缀 / 中间窗口
        （指定 to_seq）永远判不一致，而空窗口反而恒真通过——方向整个反了。
        窗口轨迹本身就是完整轨迹的连续子段（同源事件、同口径过滤），
        完整轨迹与卷宗一致即保证任意窗口输出与历史一致。
        """
        if self.dossier.case_id and self.dossier.case_id != case_id:
            raise ValueError(
                "回放引擎绑定的卷宗属于案件 %r，不能用于重放案件 %r（调用方配对错误）"
                % (self.dossier.case_id, case_id)
            )
        all_events = self.log.events(case_id)
        events = tuple(
            e for e in all_events
            if e.seq >= from_seq and (to_seq is None or e.seq <= to_seq)
        )
        trace = self.rebuild_trace(events)
        full = self.rebuild_trace(all_events)

        chain = self.log.verify()
        verification = self.dossier.verify()
        recorded = self.dossier_trace(self.dossier)

        matches = (
            full.stages == recorded.stages
            and full.signatures == recorded.signatures
            and full.opinion_ids == recorded.opinion_ids
            and full.amendments == recorded.amendments
        )

        stage_consistent = True
        if expected_stage is not None:
            stage_consistent = full.final_stage == expected_stage

        return ReplayResult(
            case_id=case_id,
            from_seq=from_seq,
            to_seq=to_seq,
            events=events,
            trace=trace,
            chain_ok=chain.ok,
            chain_reason=chain.reason,
            completeness=verification.completeness,
            missing_layers=tuple(l.value for l in verification.missing_layers),
            matches_dossier=matches,
            stage_consistent=stage_consistent,
            topology_ok=self.topology_ok(full),
        )

    @staticmethod
    def topology_ok(trace: ConclusionTrace) -> bool:
        """重放轨迹是否满足状态机拓扑。

        逐对检查相邻阶段是否在 ``TRANSITIONS`` 白名单内；再确认全局
        ``bypass_edges()`` 自检为空——即状态机本身没有绕行边。
        """
        stages = trace.stages
        for src, dst in zip(stages, stages[1:]):
            allowed = TRANSITIONS.get(src)
            if allowed is None or dst not in allowed:
                return False
        return not bypass_edges()

    # ---------------- 归因 ----------------
    def attribution(self, case_id: str) -> Dict[str, List[str]]:
        """把意见逐条归因到规则版本（支撑采纳率 / 误报率逐条统计，PRD 8.9 用途③）。"""
        buckets: Dict[str, List[str]] = {}
        for event in self.log.events(case_id):
            if event.kind != "opinion_generated":
                continue
            payload = event.payload or {}
            key = "%s@%s" % (payload.get("rule_id"), payload.get("rule_version"))
            buckets.setdefault(key, []).append(payload.get("opinion_id") or "")
        return buckets