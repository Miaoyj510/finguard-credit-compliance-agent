"""确定性判定引擎：把规则 DSL 与事实求值成「命中 / 不命中 / 不适用」。

三条不可让步的规则：

1. **判定权只在规则引擎**：模型不参与。引擎只做纯比较，不生成任何结论文字，
   结论文字来自规则里写死的 label。
2. **严重度取自规则库**：引擎不接受外部传入的严重度，输出里的 severity 一定
   是 rule.severity（PRD 6.3 FR-AUDIT-03）。
3. **引擎永不抛异常**：规则写错、事实缺失、类型不可比，一律降级成「未判定」
   并带原因码，绝不让一条坏规则把整批审查带崩（PRD 8.8 F-02）。

依据：PRD 6.1 FR-RAG-01、6.3 FR-AUDIT-02 / FR-AUDIT-03、8.8 F-02。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..domain.case import TripleKey
from ..domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from ..domain.rule import Rule
from .dsl import DslParseError, FieldRef, Literal, RuleExpression, parse_expression

__all__ = ["Evaluation", "RuleEngine"]


@dataclass(frozen=True)
class Evaluation:
    """一条规则对一个案件的判定结果（不可变）。

    verdict 与 coverage 的映射（PRD 三态是固定枚举，未判定不是第四态）：
        已判定   -> verdict = HIT / MISS
        不适用   -> verdict = NOT_APPLICABLE
        未判定   -> verdict = NOT_APPLICABLE + reason_code 非空
    **下游区分「不适用」与「未判定」必须读 coverage / reason_code，不能只看 verdict。**

    可哈希性 caveat：values 里保存事实原值，若事实值本身不可哈希（list/dict），
    hash(Evaluation) 会抛 TypeError。需要入集合/做键时先确认事实值可哈希。
    """

    rule_id: str
    rule_version: str
    verdict: Verdict
    coverage: CoverageFlag
    reason: str
    reason_code: Optional[UnresolvedReasonCode]
    fact_refs: Tuple[str, ...]
    values: Tuple[Tuple[str, Any], ...]
    severity: Severity
    hit_label: Optional[str]

    @property
    def resolved(self) -> bool:
        """是否已判定。命中与未命中都算已判定。"""
        return self.coverage is CoverageFlag.JUDGED

    def value_of(self, name: str) -> Any:
        for key, val in self.values:
            if key == name:
                return val
        raise KeyError(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "verdict": self.verdict.value,
            "coverage": self.coverage.value,
            "reason": self.reason,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "fact_refs": list(self.fact_refs),
            "values": dict(self.values),
            "severity": self.severity.value,
            "hit_label": self.hit_label,
        }


def _is_number(value: Any) -> bool:
    # bool 是 int 的子类，但语义上不是数量，单独排除
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _comparable(a: Any, b: Any) -> bool:
    if _is_number(a) and _is_number(b):
        # NaN 与任何值比较都是 False（!= 为 True），会把无效数据判成确定结论
        return not (_is_nan(a) or _is_nan(b))
    if isinstance(a, str) and isinstance(b, str):
        return True
    return False


def _compare(lhs: Any, op: str, rhs: Any) -> bool:
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    raise DslParseError("未知比较运算符：%r" % (op,))


class RuleEngine:
    """把规则 DSL 与事实求值。

    引擎是**无状态**的（除表达式缓存外），因此同一输入必得同一输出——
    这是 PRD 指标「判定可复算」的工程基础。
    """

    def __init__(self) -> None:
        self._cache: Dict[str, RuleExpression] = {}

    # ---------------- 内部 ----------------
    def _parse(self, dsl: str) -> RuleExpression:
        expr = self._cache.get(dsl)
        if expr is None:
            expr = parse_expression(dsl)
            self._cache[dsl] = expr
        return expr

    @staticmethod
    def _applies(rule: Rule, context: Optional[TripleKey]) -> bool:
        if context is None:
            return True
        return (
            rule.effective_on(context.business_date)
            and rule.covers_product(context.product_type)
            and rule.covers_org(context.org_level)
        )

    @staticmethod
    def _operand_value(operand: Any, facts: Mapping[str, Any]) -> Tuple[Any, bool]:
        """返回 (值, 是否存在)。缺失或为 None 都算缺失。"""
        if isinstance(operand, FieldRef):
            if operand.name not in facts:
                return None, False
            value = facts[operand.name]
            if value is None:
                return None, False
            return value, True
        if isinstance(operand, Literal):
            return operand.value, True
        return None, False

    # ---------------- 主入口 ----------------
    def evaluate(
        self,
        rule: Rule,
        facts: Mapping[str, Any],
        context: Optional[TripleKey] = None,
    ) -> Evaluation:
        """判定一条规则。**坏数据永不抛异常**——一律降级成未判定。

        契约边界（该抛的抛）：rule 必须是 Rule 实例，facts 必须是 Mapping
        （None 视为空事实，走 MISSING_FACT 降级）。违反类型契约属于调用方 bug，
        抛 TypeError——把它降级成「未判定」会掩盖编程错误（评审意见，切片 2）。

        context 的语义（重要）：
            * 传入 TripleKey —— 引擎会**再验一次**规则的适用范围（产品 / 机构 /
              生效期），不匹配则判「不适用」。这是对注册表绑定的防御性复核。
            * 传 None —— 表示「调用方已完成三键绑定，规则必然适用」，引擎不再
              复核范围。此时若传入未绑定的规则，引擎不会替你把关——**范围判定
              的唯一权威是 RuleStore.bind()**（PRD 6.1 FR-RAG-04）。
        """
        if not isinstance(rule, Rule):
            raise TypeError("rule 必须是 Rule 实例，得到 %r" % type(rule).__name__)
        if facts is None:
            facts = {}
        if not isinstance(facts, Mapping):
            raise TypeError("facts 必须是 Mapping（None 视为空事实），得到 %r" % type(facts).__name__)
        try:
            return self._evaluate(rule, facts, context)
        except Exception as exc:                      # 兜底：引擎自身异常也不许带崩整批
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="引擎内部异常，已降级为未判定：%s" % (exc.__class__.__name__,),
                reason_code=UnresolvedReasonCode.ENGINE_ERROR,
                fact_refs=(),
                values=(),
                severity=rule.severity,
                hit_label=None,
            )

    def _evaluate(
        self,
        rule: Rule,
        facts: Mapping[str, Any],
        context: Optional[TripleKey],
    ) -> Evaluation:
        # 1) 适用性（scope / 生效期）
        if not self._applies(rule, context):
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.NOT_APPLICABLE,
                reason="规则对该案件不适用（产品 / 机构 / 生效期不匹配）",
                reason_code=None,
                fact_refs=(),
                values=(),
                severity=rule.severity,
                hit_label=None,
            )

        # 2) 解析（解析失败 -> 未判定，不崩）
        try:
            expr = self._parse(rule.dsl)
        except DslParseError as exc:
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="表达式无法解析：%s" % (exc,),
                reason_code=UnresolvedReasonCode.DSL_PARSE_ERROR,
                fact_refs=(),
                values=(),
                severity=rule.severity,
                hit_label=None,
            )

        # 3) 取事实值（缺失 -> 未判定）
        lhs_value, lhs_ok = self._operand_value(expr.comparison.lhs, facts)
        rhs_value, rhs_ok = self._operand_value(expr.comparison.rhs, facts)
        missing = tuple(
            name for name in expr.field_names()
            if name not in facts or facts[name] is None
        )
        if not (lhs_ok and rhs_ok) or missing:
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="判定所需事实缺失：%s" % ("、".join(missing) or "未知",),
                reason_code=UnresolvedReasonCode.MISSING_FACT,
                fact_refs=missing,
                values=(),
                severity=rule.severity,
                hit_label=None,
            )

        # 3.5) NaN 单独拦截：给出明确原因，避免「float vs float 不可比」的误导文案
        if _is_nan(lhs_value) or _is_nan(rhs_value):
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="事实值为 NaN，不能参与判定：%s" % (expr.describe(),),
                reason_code=UnresolvedReasonCode.TYPE_MISMATCH,
                fact_refs=expr.field_names(),
                values=tuple((n, facts[n]) for n in expr.field_names() if n in facts),
                severity=rule.severity,
                hit_label=None,
            )

        # 4) 类型可比性（不可比 -> 未判定，而不是判 False）
        if not _comparable(lhs_value, rhs_value):
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="事实类型不可比：%s vs %s（%s）"
                % (
                    type(lhs_value).__name__,
                    type(rhs_value).__name__,
                    expr.describe(),
                ),
                reason_code=UnresolvedReasonCode.TYPE_MISMATCH,
                fact_refs=expr.field_names(),
                values=tuple((n, facts[n]) for n in expr.field_names() if n in facts),
                severity=rule.severity,
                hit_label=None,
            )

        # 5) 比较
        try:
            hit = _compare(lhs_value, expr.comparison.op, rhs_value)
        except TypeError:
            return Evaluation(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                verdict=Verdict.NOT_APPLICABLE,
                coverage=CoverageFlag.UNRESOLVED,
                reason="比较执行失败，已降级为未判定",
                reason_code=UnresolvedReasonCode.TYPE_MISMATCH,
                fact_refs=expr.field_names(),
                values=tuple((n, facts[n]) for n in expr.field_names() if n in facts),
                severity=rule.severity,
                hit_label=None,
            )

        values = tuple((n, facts[n]) for n in expr.field_names() if n in facts)
        return Evaluation(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            verdict=Verdict.HIT if hit else Verdict.MISS,
            coverage=CoverageFlag.JUDGED,
            reason=expr.label if hit else "条件不成立",
            reason_code=None,
            fact_refs=expr.field_names(),
            values=values,
            severity=rule.severity,
            hit_label=expr.label if hit else None,
        )

    def evaluate_all(
        self,
        rules: Iterable[Rule],
        facts: Mapping[str, Any],
        context: Optional[TripleKey] = None,
    ) -> Tuple[Evaluation, ...]:
        """批量判定。顺序与入参一致，便于逐条核对。"""
        return tuple(self.evaluate(rule, facts, context) for rule in rules)
