"""案件与三键（PRD 第四章 4.3、第五章 5.3）。

三键 = 业务发生日 × 产品类型 × 机构层级，是规则版本选择的唯一依据。
案件受理时三键即被锁定，之后不可修改——否则同一笔业务的结论不可复算。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

CASE_ID_PREFIX = "FG"


@dataclass(frozen=True)
class TripleKey:
    """规则版本三键。"""

    business_date: date   # 业务发生日
    product_type: str    # 产品类型
    org_level: str       # 机构层级

    def __post_init__(self) -> None:
        if not self.product_type:
            raise ValueError("三键缺少 product_type")
        if not self.org_level:
            raise ValueError("三键缺少 org_level")

    def to_dict(self) -> dict:
        return {
            "business_date": self.business_date.isoformat(),
            "product_type": self.product_type,
            "org_level": self.org_level,
        }

    def __str__(self) -> str:
        return "%s | %s | %s" % (self.business_date.isoformat(), self.product_type, self.org_level)


@dataclass
class CaseState:
    """案件状态（PRD 8.4）。

    切片 1 阶段只保留骨架：案件号、三键、当前阶段、规则版本绑定结果。
    后续切片逐步补齐 facts_snapshot / coverage / gates / 事件日志。
    """

    case_id: str
    triple_key: TripleKey
    stage: str = "S0_RECEIVED"
    rule_binding: tuple = ()          # tuple[RuleBinding, ...]（切片 2 起填充）
    unresolved: tuple = ()            # tuple[UnresolvedRule, ...]
    created_at: Optional[str] = None

    @classmethod
    def create(cls, case_id: str, triple_key: TripleKey) -> "CaseState":
        return cls(case_id=case_id, triple_key=triple_key)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "triple_key": self.triple_key.to_dict(),
            "stage": self.stage,
            "rule_binding": [b.to_dict() for b in self.rule_binding],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "created_at": self.created_at,
        }
