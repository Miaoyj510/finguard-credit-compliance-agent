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

__all__ = ["ConclusionTrace", "ReplayResult", "ReplayEngine"]


@dataclass(frozen=True)
class ConclusionTrace:
    """重放重建出的结论轨迹。"""

    stages: Tuple[str, ...] = ()
    signatures: Tuple[Tuple[str, str, str], ...] = ()
    opinion_ids: Tuple[str, ...] = ()
    final_stage: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stages": list(self.stages),
            "signatures": [list(s) for s in self.signatures],
            "opinion_ids": list(self.opinion_ids),
            "final_stage": self.final_stage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConclusionTrace":
        return cls(
            stages=tuple(data.get("stages", ())),
            signatures=tuple(tuple(s) for s in data.get("signatures", ())),
            opinion_ids=tuple(data.get("opinion_ids", ())),
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

    @property
    def usable_for_audit(self) -> bool:
        """能否用于监管取证：链完整 + 六层齐全 + 轨迹与卷宗一致，三者缺一不可。"""
        return (
            self.chain_ok
            and self.completeness is Completeness.COMPLETE
            and self.matches_dossier
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
        return ConclusionTrace(
            stages=tuple(stages),
            signatures=tuple(signatures),
            opinion_ids=tuple(opinions),
            final_stage=stages[-1] if stages else "",
        )

    @staticmethod
    def dossier_trace(dossier: Dossier) -> ConclusionTrace:
        """从卷宗读出当时记录的轨迹（L4 意见 + L5 签核与阶段轨迹）。"""
        stages: Tuple[str, ...] = ()
        signatures: List[Tuple[str, str, str]] = []
        opinions: Tuple[str, ...] = ()
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
            final_stage=stages[-1] if stages else "",
        )

    @staticmethod
    def _suffix_matches(replayed: Sequence[Any], recorded: Sequence[Any]) -> bool:
        """重放区间可能只是全程的后缀，因此与卷宗轨迹**按后缀比对**。"""
        if not replayed:
            return True
        if len(replayed) > len(recorded):
            return False
        return tuple(recorded[len(recorded) - len(replayed):]) == tuple(replayed)

    # ---------------- 回放 ----------------
    def replay(
        self,
        case_id: str,
        from_seq: int = 1,
        to_seq: Optional[int] = None,
    ) -> ReplayResult:
        """按 seq 重放指定案件，返回结论轨迹与可取证性判定。"""
        events = self.log.since(case_id, from_seq)
        if to_seq is not None:
            events = tuple(e for e in events if e.seq <= to_seq)
        trace = self.rebuild_trace(events)

        chain = self.log.verify()
        verification = self.dossier.verify()
        recorded = self.dossier_trace(self.dossier)

        matches = (
            self._suffix_matches(trace.stages, recorded.stages)
            and self._suffix_matches(trace.signatures, recorded.signatures)
            and self._suffix_matches(trace.opinion_ids, recorded.opinion_ids)
        )

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
        )

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