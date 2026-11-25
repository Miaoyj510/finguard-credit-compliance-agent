"""规则版本注册表与三键取版本（PRD 5.3、FR-RAG-03 / 04 / 05）。

这是本项目最核心的一段逻辑：**按业务发生日取到唯一一版规则，取不到就说不**。
绝不按最新版硬判——那是合规事故的来源，不是效率优化。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Sequence, Tuple

from ..domain.enums import UnresolvedReasonCode
from ..domain.rule import Rule


@dataclass(frozen=True)
class RuleBinding:
    """一条规则与本案件的绑定结果。"""

    rule_id: str
    version: str
    effective_from: date
    effective_to: object  # Optional[date]
    binding_basis: str   # 三键唯一命中

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "binding_basis": self.binding_basis,
        }


@dataclass(frozen=True)
class UnresolvedRule:
    """未能唯一绑定版本的规则（须人工判定）。"""

    rule_id: str
    reason_code: UnresolvedReasonCode
    detail: str
    candidates: Tuple[str, ...] = ()   # 候选版本标识，便于人工裁定

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class BindingResult:
    """三键取版本的完整结果。"""

    bindings: Tuple[RuleBinding, ...]
    unresolved: Tuple[UnresolvedRule, ...]

    @property
    def bound_rule_ids(self) -> Tuple[str, ...]:
        return tuple(b.rule_id for b in self.bindings)

    def version_of(self, rule_id: str) -> str:
        for b in self.bindings:
            if b.rule_id == rule_id:
                return b.version
        raise KeyError("规则 %s 未绑定" % rule_id)

    def to_dict(self) -> dict:
        return {
            "bindings": [b.to_dict() for b in self.bindings],
            "unresolved": [u.to_dict() for u in self.unresolved],
        }


class RuleStore:
    """规则版本注册表。

    职责边界：
    - 只负责「哪一版适用」，不负责「判定结果」（判定在切片 2 的 DSL 引擎）；
    - 只负责「可不可以算出来」，不负责「谁来签字」（签核在切片 5 的 Harness）；
    - 不修改规则内容，只建立索引与版本选择。
    """

    def __init__(self, rules: Iterable[Rule]) -> None:
        self._rules: List[Rule] = list(rules)
        self._by_rule_id: Dict[str, List[Rule]] = {}
        seen = set()
        for rule in self._rules:
            key = rule.identity
            if key in seen:
                raise ValueError("规则版本重复：%s（同一 rule_id + version 只能有一条）" % key)
            seen.add(key)
            self._by_rule_id.setdefault(rule.rule_id, []).append(rule)
        for versions in self._by_rule_id.values():
            versions.sort(key=lambda r: (r.effective_from, r.version))

    # ---------------- 查询 ----------------
    def all_rules(self) -> Tuple[Rule, ...]:
        return tuple(self._rules)

    def rule_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._by_rule_id.keys()))

    def versions_of(self, rule_id: str) -> Tuple[Rule, ...]:
        return tuple(self._by_rule_id.get(rule_id, ()))

    # ---------------- 三键取版本（核心） ----------------
    def bind(self, business_date: date, product_type: str, org_level: str) -> BindingResult:
        """按「业务发生日 × 产品类型 × 机构层级」取唯一适用版本。

        返回 (bindings, unresolved)：
        - 恰好 1 个版本命中 → 进入 bindings
        - 0 个命中 → unresolved / NO_VERSION_MATCH
        - ≥ 2 个命中（生效区间重叠）→ unresolved / AMBIGUOUS_VERSION
        """
        bindings: List[RuleBinding] = []
        unresolved: List[UnresolvedRule] = []

        for rule_id in sorted(self._by_rule_id.keys()):
            matched = [
                r
                for r in self._by_rule_id[rule_id]
                if r.effective_on(business_date)
                and r.covers_product(product_type)
                and r.covers_org(org_level)
            ]
            if len(matched) == 1:
                r = matched[0]
                bindings.append(
                    RuleBinding(
                        rule_id=r.rule_id,
                        version=r.version,
                        effective_from=r.effective_from,
                        effective_to=r.effective_to,
                        binding_basis="三键唯一命中",
                    )
                )
            elif len(matched) == 0:
                unresolved.append(
                    UnresolvedRule(
                        rule_id=rule_id,
                        reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                        detail="业务发生日 %s / 产品 %s / 机构 %s 下无生效版本"
                        % (business_date.isoformat(), product_type, org_level),
                    )
                )
            else:
                unresolved.append(
                    UnresolvedRule(
                        rule_id=rule_id,
                        reason_code=UnresolvedReasonCode.AMBIGUOUS_VERSION,
                        detail="命中 %d 个重叠版本，需人工裁定" % len(matched),
                        candidates=tuple(r.identity for r in matched),
                    )
                )

        return BindingResult(bindings=tuple(bindings), unresolved=tuple(unresolved))

    def bind_case(self, case) -> "object":
        """对 CaseState 做绑定并写回 state。返回 CaseState。"""
        result = self.bind(
            case.triple_key.business_date,
            case.triple_key.product_type,
            case.triple_key.org_level,
        )
        case.rule_binding = result.bindings
        case.unresolved = result.unresolved
        return case
