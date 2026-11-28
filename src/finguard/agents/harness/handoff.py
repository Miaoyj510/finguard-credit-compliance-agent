"""Handoff 契约（PRD 8.6 Handoff 必填字段表）。

Handoff 是 Agent 之间传递上下文的结构化契约。规矩只有一条：

    **缺失必填字段即中断该交接，转入异常队列并告警，不允许用默认值补齐。**

这与"用默认值兜底"的常见做法相反：默认值会让下游误以为上下文完整，
而人在环系统里"看起来完整"比"明确缺失"危险得多。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .states import HandoffIncompleteError, Stage

__all__ = [
    "HANDOFF_REQUIRED_FIELDS",
    "AGENT_ROLES",
    "TOPOLOGY_EDGES",
    "Handoff",
]

#: PRD 8.6 的 14 个必填字段（顺序即校验顺序）
HANDOFF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "handoff_id",
    "case_id",
    "from_agent",
    "to_agent",
    "stage",
    "rule_binding",
    "facts_digest",
    "citation_bundle",
    "open_questions",
    "coverage_summary",
    "human_gate_state",
    "produced_at",
    "producer_spec_version",
    "severity_source",
)

#: 五个 Agent 角色（PRD 8.2：审查 / 风险研判 / 贷后监测 / 核查 / 签核与归档）
AGENT_ROLES: Tuple[str, ...] = (
    "审查 Agent",
    "风险研判 Agent",
    "贷后监测 Agent",
    "核查 Agent",
    "签核与归档 Agent",
)

#: 编排拓扑里的合法交接边（from -> to）
TOPOLOGY_EDGES: Tuple[Tuple[str, str], ...] = (
    ("审查 Agent", "风险研判 Agent"),
    ("风险研判 Agent", "签核与归档 Agent"),
    ("签核与归档 Agent", "贷后监测 Agent"),
    ("贷后监测 Agent", "核查 Agent"),
    ("核查 Agent", "签核与归档 Agent"),
)

#: 严重度来源唯一合法取值（PRD 8.6：非此值拒收）
SEVERITY_SOURCE = "rule_store"


@dataclass(frozen=True)
class Handoff:
    """一次 Agent 交接的上下文包。

    字段可以缺——缺了正是 validate() 要拦的。
    """

    handoff_id: Optional[str] = None
    case_id: Optional[str] = None
    from_agent: Optional[str] = None
    to_agent: Optional[str] = None
    stage: Optional[Any] = None
    rule_binding: Optional[Tuple[Mapping[str, Any], ...]] = None
    facts_digest: Optional[Tuple[Mapping[str, Any], ...]] = None
    citation_bundle: Optional[Tuple[Mapping[str, Any], ...]] = None
    open_questions: Optional[Tuple[Mapping[str, Any], ...]] = None
    coverage_summary: Optional[Mapping[str, Any]] = None
    human_gate_state: Optional[Mapping[str, Any]] = None
    produced_at: Optional[str] = None
    producer_spec_version: Optional[str] = None
    severity_source: Optional[str] = None

    # ---------------- 校验 ----------------
    def missing_fields(self) -> Tuple[str, ...]:
        """返回缺失的必填字段名（空元组 = 齐全）。

        数组类字段里，rule_binding 与 facts_digest **必须非空**：
        没有适用规则或没有事实摘要的交接，等于把上下文丢了。
        citation_bundle 与 open_questions 可以是空数组——"确实没有依据"
        与"没有传依据"是两件事，必须能区分。
        """
        missing: List[str] = []
        for name in HANDOFF_REQUIRED_FIELDS:
            value = getattr(self, name)
            if value is None:
                missing.append(name)
                continue
            if name in ("rule_binding", "facts_digest") and len(value) == 0:
                missing.append(name)
        return tuple(missing)

    def validate(self) -> None:
        """校验交接契约。不通过抛 HandoffIncompleteError（交接中断）。"""
        missing = self.missing_fields()
        if missing:
            raise HandoffIncompleteError(
                "Handoff 缺少必填字段：%s（不得用默认值补齐）" % "、".join(missing)
            )

        if not isinstance(self.stage, Stage):
            raise HandoffIncompleteError(
                "Handoff.stage 非法：%r（须取自 8.5 状态清单）" % (self.stage,)
            )
        if self.severity_source != SEVERITY_SOURCE:
            raise HandoffIncompleteError(
                "Handoff.severity_source 非法：%r（唯一合法取值 %s）"
                % (self.severity_source, SEVERITY_SOURCE)
            )
        if self.from_agent not in AGENT_ROLES or self.to_agent not in AGENT_ROLES:
            raise HandoffIncompleteError(
                "Handoff 交接方非法：%r -> %r（须为编排拓扑中的角色）"
                % (self.from_agent, self.to_agent)
            )
        if (self.from_agent, self.to_agent) not in TOPOLOGY_EDGES:
            raise HandoffIncompleteError(
                "Handoff 交接边不在编排拓扑中：%r -> %r" % (self.from_agent, self.to_agent)
            )

        for item in self.rule_binding or ():
            if not item.get("rule_id"):
                raise HandoffIncompleteError("Handoff.rule_binding 每项须含 rule_id")
            basis = item.get("binding_basis") or ""
            if "须人工判定" in basis or item.get("unresolved_reason_code"):
                # 未判定项**本来就没有唯一版本**（NO_VERSION_MATCH 无版本可写，
                # AMBIGUOUS_VERSION 多版本待裁），强制 version 会逼调用方填假值；
                # 它的替代性完备条件是原因码必填。
                if not item.get("unresolved_reason_code"):
                    raise HandoffIncompleteError(
                        "Handoff.rule_binding 未判定项须标 unresolved_reason_code"
                    )
            elif not item.get("version"):
                raise HandoffIncompleteError("Handoff.rule_binding 已绑定项须含 version")

        for item in self.facts_digest or ():
            for key in ("field_path", "value", "confidence", "conflict_state"):
                if key not in item:
                    raise HandoffIncompleteError(
                        "Handoff.facts_digest 每项须含 %s" % key
                    )

        for item in self.citation_bundle or ():
            for key in ("doc_id", "chunk_id", "source_ref", "quote"):
                if key not in item:
                    raise HandoffIncompleteError(
                        "Handoff.citation_bundle 每项须含 %s" % key
                    )

        for item in self.open_questions or ():
            for key in ("question_id", "reason_code"):
                if key not in item:
                    raise HandoffIncompleteError(
                        "Handoff.open_questions 每项须含 %s" % key
                    )

        summary = self.coverage_summary or {}
        for key in ("judged", "not_applicable", "unresolved", "total"):
            if key not in summary:
                raise HandoffIncompleteError("Handoff.coverage_summary 缺 %s" % key)
        total = summary["judged"] + summary["not_applicable"] + summary["unresolved"]
        if total != summary["total"]:
            raise HandoffIncompleteError(
                "Handoff.coverage_summary 三类之和 %d 不等于 total %d" % (total, summary["total"])
            )

        gate_state = self.human_gate_state or {}
        # PRD 8.6：human_gate_state「必含 gate_id / state / 是否有签核记录」三项
        for key in ("gate_id", "state", "has_signature"):
            if key not in gate_state:
                raise HandoffIncompleteError("Handoff.human_gate_state 缺 %s" % key)

    # ---------------- 输出 ----------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "case_id": self.case_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "stage": self.stage.value if isinstance(self.stage, Stage) else self.stage,
            "rule_binding": [dict(i) for i in (self.rule_binding or ())],
            "facts_digest": [dict(i) for i in (self.facts_digest or ())],
            "citation_bundle": [dict(i) for i in (self.citation_bundle or ())],
            "open_questions": [dict(i) for i in (self.open_questions or ())],
            "coverage_summary": dict(self.coverage_summary or {}),
            "human_gate_state": dict(self.human_gate_state or {}),
            "produced_at": self.produced_at,
            "producer_spec_version": self.producer_spec_version,
            "severity_source": self.severity_source,
            "missing_fields": list(self.missing_fields()),
        }