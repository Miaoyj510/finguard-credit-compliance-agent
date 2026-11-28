"""审查意见模型（PRD 6.3 FR-AUDIT-01 ~ 07）。

意见分两种形态，**区别在于有没有通过引用闸门**：

    OpinionDraft   —— 生成阶段的草稿。模型（或桩件）产出，可能缺引用三件套，
                      会被闸门拦下，**不进输出通道**（FR-AUDIT-07）。
    ReviewOpinion  —— 通过闸门后的正式意见，进入签核与卷宗。

另有两张统计面：
    CoverageSummary —— 覆盖度三类计数，必须自洽（已判定 + 不适用 + 未判定 = 规则总数）。
    UnresolvedEntry —— 未判定条目，带原因码（FR-AUDIT-02）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .enums import CoverageFlag, Severity, UnresolvedReasonCode

__all__ = [
    "OpinionOrigin",
    "OpinionDraft",
    "FactCitation",
    "ReviewOpinion",
    "BlockedOpinion",
    "SeverityCorrection",
    "UnresolvedEntry",
    "CoverageSummary",
]


@dataclass(frozen=True)
class FactCitation:
    """事实引用三元组（PRD 8.1 citation_requirement / FR-AUDIT-06）。

    每项引用必须含 field_path / value / source_channel——只有字段名
    不够：签核与回放（切片 6）需要判定当时的取值与来源通道快照。
    """

    field_path: str
    value: Any
    source_channel: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path,
            "value": self.value,
            "source_channel": list(self.source_channel),
        }


class OpinionOrigin(str, Enum):
    """意见来源。"""

    RULE_ENGINE = "rule_engine"   # 由判定结果直接派生
    MODEL = "model"               # 由模型生成理由（需过闸门）
    HUMAN = "human"               # 人工撰写（PRD 8.4 的 HUMAN_CREATED）
    # 人工改写 AI 意见（PRD 8.4 的 HUMAN_AMENDED）。标记了这个 origin 的意见，
    # 切片 6 组装卷宗时**必须**能找到对应的 amendment 记录（含双版本与理由），
    # 否则组装直接失败——FR-GATE-06 的强制力落在卷宗层，不靠自觉登记。
    HUMAN_AMENDED = "human_amended"


@dataclass(frozen=True)
class OpinionDraft:
    """生成阶段的意见草稿。字段可以缺——缺了正是闸门要拦的。"""

    rule_id: str
    rule_version: str
    severity: Severity
    fact_refs: Tuple[str, ...] = ()
    rationale: str = ""
    quotes: Tuple[str, ...] = ()
    origin: OpinionOrigin = OpinionOrigin.MODEL


@dataclass(frozen=True)
class ReviewOpinion:
    """通过闸门后的正式审查意见。"""

    opinion_id: str
    rule_id: str
    rule_version: str
    severity: Severity
    fact_refs: Tuple[str, ...]
    rationale: str
    coverage: CoverageFlag
    quotes: Tuple[str, ...] = ()
    unresolved_reason_code: Optional[UnresolvedReasonCode] = None
    origin: OpinionOrigin = OpinionOrigin.MODEL
    #: 引用三元组（field_path / value / source_channel），与 fact_refs 一一对应
    fact_citations: Tuple[FactCitation, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opinion_id": self.opinion_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "fact_refs": list(self.fact_refs),
            "fact_citations": [c.to_dict() for c in self.fact_citations],
            "rationale": self.rationale,
            "coverage": self.coverage.value,
            "quotes": list(self.quotes),
            "unresolved_reason_code": self.unresolved_reason_code.value if self.unresolved_reason_code else None,
            "origin": self.origin.value,
        }


@dataclass(frozen=True)
class BlockedOpinion:
    """被引用闸门或禁止行为拦下的草稿。**不进输出通道**，但必须留痕。"""

    rule_id: str
    rule_version: str
    reason: str
    missing: Tuple[str, ...] = ()
    forbidden: Tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "reason": self.reason,
            "missing": list(self.missing),
            "forbidden": list(self.forbidden),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SeverityCorrection:
    """严重度被强制替换回规则库值的记录（FR-AUDIT-03 / FB-04）。"""

    rule_id: str
    rule_version: str
    proposed: Severity
    applied: Severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "proposed": self.proposed.value,
            "applied": self.applied.value,
        }


@dataclass(frozen=True)
class UnresolvedEntry:
    """未判定条目：带原因码，计入覆盖度。"""

    rule_id: str
    rule_version: str
    reason_code: UnresolvedReasonCode
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CoverageSummary:
    """覆盖度三类计数。自洽条件：三类之和 == 规则总数（每条规则有且仅归入一类）。

    **口径提醒（评审留档 A1）**：``judged`` 计的是**引擎判定数**，
    因此 ``judged`` 通常 **大于** ``len(opinions)``——被引用闸门或禁止行为
    拦下的意见**不进 opinions**，但它仍然是"已判定"的规则条目。
    两个数字口径不同，比较时不要混用。

    ``bound_rule_total`` 是本案实际绑定的规则条数（切片 5 A4 交叉校验）：
    与 ``total`` 不一致即说明判定集合与规则绑定集合对不上，``is_consistent`` 判否。
    """

    judged: int = 0
    not_applicable: int = 0
    unresolved: int = 0
    total: int = 0
    unresolved_entries: Tuple[UnresolvedEntry, ...] = ()
    # 本案实际绑定的规则条数（切片 5 A4）：与 total 不一致说明判定集合与规则集合对不上。
    bound_rule_total: Optional[int] = None

    @property
    def is_consistent(self) -> bool:
        if self.judged + self.not_applicable + self.unresolved != self.total:
            return False
        if self.bound_rule_total is not None and self.bound_rule_total != self.total:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "judged": self.judged,
            "not_applicable": self.not_applicable,
            "unresolved": self.unresolved,
            "total": self.total,
            "bound_rule_total": self.bound_rule_total,
            "is_consistent": self.is_consistent,
            "unresolved_entries": [e.to_dict() for e in self.unresolved_entries],
        }
