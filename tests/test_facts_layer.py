"""事实层编排（PRD 6.2 FR-FACT-01 ~ FR-FACT-07）。"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.domain.case import TripleKey
from finguard.domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from finguard.domain.rule import Rule
from finguard.facts.extract import RouteResult
from finguard.facts.layer import ConclusionTermError, FactLayer
from finguard.facts.models import Confidence, SourceChannel
from finguard.rules.engine import RuleEngine


def route(route, values, channel=SourceChannel.OCR):
    return RouteResult(route=route, channel=channel, values=dict(values))


GOOD_A = {
    "monthly_revenue": 820000,
    "declared_revenue": 1000000,
    "flow_6m_total": 950000,
    "tenor_months": 24,
    "approval_limit": 1000000,
    "approver_authority": 2000000,
    "loan_amount": 900000,
    "industry": "零售",
}


class TestBuild(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = FactLayer()

    def test_一致事实进入判定链(self):
        factset, queue = self.layer.build(route("vote", GOOD_A), route("rule_check", GOOD_A))
        self.assertIs(factset.fields["monthly_revenue"].confidence, Confidence.HIGH)
        self.assertIn("monthly_revenue", factset.decision_ready())
        self.assertEqual(len(queue.pending()), 0)

    def test_不一致事实被拦截在判定链外(self):
        a = dict(GOOD_A)
        b = dict(GOOD_A)
        b["monthly_revenue"] = 615000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        self.assertIs(factset.fields["monthly_revenue"].confidence, Confidence.LOW)
        self.assertNotIn("monthly_revenue", factset.decision_ready())
        self.assertIn("monthly_revenue", factset.blocked_fields())

    def test_不一致产生冲突项并列两个取值(self):
        a = dict(GOOD_A); b = dict(GOOD_A); b["monthly_revenue"] = 615000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        pending = queue.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(set(pending[0].candidates), {820000, 615000})

    def test_交叉核对不通过产生冲突项(self):
        a = dict(GOOD_A); b = dict(GOOD_A)
        a["flow_6m_total"] = 500000
        b["flow_6m_total"] = 500000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        pending = queue.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].kind.value, "cross_check")

    def test_核对项被记录(self):
        factset, queue = self.layer.build(route("vote", GOOD_A), route("rule_check", GOOD_A))
        self.assertTrue(len(factset.checks) >= 2)

    def test_事实缺失不产生冲突(self):
        """缺失由判定引擎按 MISSING_FACT 处理，事实层不重复报冲突。"""
        partial = {"monthly_revenue": 100}
        factset, queue = self.layer.build(route("vote", partial), route("rule_check", partial))
        self.assertEqual(len(queue.pending()), 0)


class TestConclusionGate(unittest.TestCase):
    """FR-FACT-07：事实层不输出任何结论性判定。"""

    def setUp(self) -> None:
        self.layer = FactLayer()

    def test_正常取值不误报(self):
        values = {"industry": "房地产", "risk_note": "高风险行业"}
        factset, _ = self.layer.build(route("vote", values), route("rule_check", values))
        self.assertEqual(factset.conclusion_hits(), [])

    def test_取值含结论词直接失败(self):
        values = {"review_result": "该笔业务违规"}
        with self.assertRaises(ConclusionTermError):
            self.layer.build(route("vote", values), route("rule_check", values))

    def test_字段名含结论词也失败(self):
        values = {"is_compliant": "是"}
        with self.assertRaises(ConclusionTermError):
            self.layer.build(route("vote", values), route("rule_check", values))


class TestAdjudication(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = FactLayer()

    def test_裁决后字段放行(self):
        a = dict(GOOD_A); b = dict(GOOD_A); b["monthly_revenue"] = 615000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        self.assertNotIn("monthly_revenue", factset.decision_ready())
        factset2 = self.layer.adjudicate(
            factset, queue, "low_confidence:monthly_revenue", 820000, "以对账单为准", "张合规")
        self.assertIn("monthly_revenue", factset2.decision_ready())
        self.assertEqual(len(queue.pending()), 0)


class TestWithEngineIntegration(unittest.TestCase):
    """跨切片集成：低置信事实被拦在判定链外 -> 规则判「未判定」。"""

    def test_低置信字段导致规则未判定(self):
        layer = FactLayer()
        a = dict(GOOD_A); b = dict(GOOD_A); b["monthly_revenue"] = 615000
        factset, queue = layer.build(route("vote", a), route("rule_check", b))

        rule = Rule(
            rule_id="R-CR-013", version="3.2.0",
            effective_from=date(2025, 7, 1), effective_to=None,
            product_scope=("小额经营贷",), org_scope=("*",),
            severity=Severity.MAJOR,
            dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('不符')",
            required_facts=("monthly_revenue", "declared_revenue"),
            source_ref="《信贷业务审查操作规程》第四条（演示用合成条款）",
        )
        r = RuleEngine().evaluate(rule, factset.decision_ready())
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.MISSING_FACT)

    def test_一致事实让规则正常命中(self):
        layer = FactLayer()
        factset, queue = layer.build(route("vote", GOOD_A), route("rule_check", GOOD_A))
        rule = Rule(
            rule_id="R-CR-013", version="3.2.0",
            effective_from=date(2025, 7, 1), effective_to=None,
            product_scope=("小额经营贷",), org_scope=("*",),
            severity=Severity.MAJOR,
            dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('不符')",
            required_facts=("monthly_revenue", "declared_revenue"),
            source_ref="《信贷业务审查操作规程》第四条（演示用合成条款）",
        )
        r = RuleEngine().evaluate(rule, factset.decision_ready())
        self.assertIs(r.verdict, Verdict.HIT)


if __name__ == "__main__":
    unittest.main()
