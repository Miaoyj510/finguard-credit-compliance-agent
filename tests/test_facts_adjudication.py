"""裁决语义专项测试（实现方对评审意见 8 的决策落地）。

意见 8：核对冲突（CC）裁决 = 单字段覆写。实现的决策是——
**CROSS_CHECK 类冲突的裁决不改写任何事实取值**：

    · LOW_CONFIDENCE：两个候选是同一字段的两个抽取值 → 人工择一，覆写（合理）
    · CROSS_CHECK：两个候选是**两个不同的事实**（申报月营收 / 近 6 月流水合计），
      择一覆写会静默改掉「客户申报了什么」的语义 → 不覆写，只解除阻塞，
      裁决结论记入核对说明。

单独成文件，避免与评审方改过的 test_facts_layer.py 冲突。
"""
from __future__ import annotations

import unittest

from finguard.facts.conflict import ConflictKind
from finguard.facts.crosscheck import (
    FIELD_APPROVAL_LIMIT,
    FIELD_APPROVER_AUTHORITY,
    FIELD_DECLARED_REVENUE,
    FIELD_FLOW_6M_TOTAL,
)
from finguard.facts.extract import RouteResult
from finguard.facts.layer import FactLayer
from finguard.facts.models import SourceChannel


def route(r, values):
    return RouteResult(route=r, channel=SourceChannel.OCR, values=dict(values))


BASE = {
    "monthly_revenue": 820000,
    "declared_revenue": 1000000,
    "flow_6m_total": 950000,
    "tenor_months": 24,
    "approval_limit": 1000000,
    "approver_authority": 2000000,
    "loan_amount": 900000,
}


class TestCrossCheckAdjudication(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = FactLayer()

    def _conflicting(self):
        """申报营收 100 万 vs 流水 10 万 → CC-01 冲突。"""
        a = dict(BASE); b = dict(BASE)
        a["flow_6m_total"] = 100000
        b["flow_6m_total"] = 100000
        return self.layer.build(route("vote", a), route("rule_check", b))

    def test_勾稽冲突裁决不改写申报事实(self):
        """裁决后 declared_revenue 必须保持原值，不能被流水合计顶掉。"""
        factset, queue = self._conflicting()
        before = factset.fields[FIELD_DECLARED_REVENUE].value
        pending = [c for c in queue.pending() if c.kind is ConflictKind.CROSS_CHECK]
        self.assertEqual(len(pending), 1)

        factset2 = self.layer.adjudicate(
            factset, queue, pending[0].conflict_id,
            before, "客户申报为准，流水未完整入账", "张合规",
        )
        self.assertEqual(factset2.fields[FIELD_DECLARED_REVENUE].value, before)
        self.assertEqual(factset2.fields[FIELD_FLOW_6M_TOTAL].value, 100000)

    def test_勾稽裁决后相关字段解除阻塞(self):
        factset, queue = self._conflicting()
        pending = [c for c in queue.pending() if c.kind is ConflictKind.CROSS_CHECK]
        factset2 = self.layer.adjudicate(
            factset, queue, pending[0].conflict_id, 1000000, "客户申报为准", "张合规")
        self.assertNotIn(FIELD_DECLARED_REVENUE, factset2.blocked_fields())
        self.assertNotIn(FIELD_FLOW_6M_TOTAL, factset2.blocked_fields())
        self.assertIn(FIELD_DECLARED_REVENUE, factset2.decision_ready())

    def test_裁决结论写进核对说明(self):
        factset, queue = self._conflicting()
        pending = [c for c in queue.pending() if c.kind is ConflictKind.CROSS_CHECK]
        factset2 = self.layer.adjudicate(
            factset, queue, pending[0].conflict_id, 1000000, "客户申报为准", "张合规")
        cc01 = [c for c in factset2.checks if c.check_id == "CC-01"]
        self.assertEqual(len(cc01), 1)
        self.assertIn("张合规", cc01[0].detail)
        self.assertIn("客户申报为准", cc01[0].detail)

    def test_偏离是客观事实passed不因裁决变true(self):
        """偏离确实存在，人工裁决不改变客观核对结果，只记录结论。"""
        factset, queue = self._conflicting()
        pending = [c for c in queue.pending() if c.kind is ConflictKind.CROSS_CHECK]
        factset2 = self.layer.adjudicate(
            factset, queue, pending[0].conflict_id, 1000000, "客户申报为准", "张合规")
        cc01 = [c for c in factset2.checks if c.check_id == "CC-01"][0]
        self.assertFalse(cc01.passed)


class TestLowConfidenceAdjudication(unittest.TestCase):
    """LOW_CONFIDENCE 是同一字段的两个抽取值 —— 人工择一后照常覆写。"""

    def setUp(self) -> None:
        self.layer = FactLayer()

    def test_低置信裁决后字段被覆写为裁决值(self):
        a = dict(BASE); b = dict(BASE)
        b["monthly_revenue"] = 615000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        pending = [c for c in queue.pending() if c.kind is ConflictKind.LOW_CONFIDENCE]
        self.assertEqual(len(pending), 1)

        factset2 = self.layer.adjudicate(
            factset, queue, pending[0].conflict_id, 820000, "以对账单为准", "张合规")
        self.assertEqual(factset2.fields["monthly_revenue"].value, 820000)
        self.assertIn("monthly_revenue", factset2.decision_ready())

    def test_裁决后带时间戳(self):
        """评审留档 A2：裁决必须留时间戳（回放需要）。"""
        a = dict(BASE); b = dict(BASE)
        b["monthly_revenue"] = 615000
        factset, queue = self.layer.build(route("vote", a), route("rule_check", b))
        pending = [c for c in queue.pending() if c.kind is ConflictKind.LOW_CONFIDENCE]
        self.layer.adjudicate(factset, queue, pending[0].conflict_id, 820000, "以对账单为准", "张合规")
        item = queue.get(pending[0].conflict_id)
        self.assertIsNotNone(item.adjudicated_at)
        self.assertTrue(item.adjudicated_at)


class TestStrictSwitchRemoved(unittest.TestCase):
    """评审留档 A1：strict=False 开关已移除——能关掉产品不变量的开关本身是风险面。"""

    def test_FactLayer不再接受strict参数(self):
        import inspect

        from finguard.facts.layer import FactLayer

        sig = inspect.signature(FactLayer.__init__)
        self.assertNotIn("strict", sig.parameters)
        self.assertNotIn("strict", sig.parameters)


if __name__ == "__main__":
    unittest.main()
