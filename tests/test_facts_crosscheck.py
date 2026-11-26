"""交叉一致性核对（PRD 6.2 FR-FACT-04 / FR-FACT-05）。"""
from __future__ import annotations

import unittest

from finguard.facts.crosscheck import CrossChecker


class TestRevenueVsFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = CrossChecker(deviation_threshold=0.20)

    def test_偏离在阈值内通过(self):
        r = self.checker.check_revenue_vs_flow({"declared_revenue": 1000000, "flow_6m_total": 950000})
        self.assertTrue(r.passed)
        self.assertAlmostEqual(r.deviation, 0.05)

    def test_偏离超阈值不通过(self):
        r = self.checker.check_revenue_vs_flow({"declared_revenue": 1000000, "flow_6m_total": 700000})
        self.assertFalse(r.passed)
        self.assertAlmostEqual(r.deviation, 0.30)
        self.assertIn("偏离", r.detail)

    def test_事实缺失跳过不造冲突(self):
        self.assertIsNone(self.checker.check_revenue_vs_flow({"declared_revenue": 1000}))
        self.assertIsNone(self.checker.check_revenue_vs_flow({}))

    def test_布尔不当数字(self):
        self.assertIsNone(self.checker.check_revenue_vs_flow(
            {"declared_revenue": True, "flow_6m_total": 100}))

    def test_申报营收为零不再静默跳过(self):
        """评审意见 7：零申报 + 非零流水是高危组合，必须产出冲突（FR-FACT-04）。"""
        r = self.checker.check_revenue_vs_flow(
            {"declared_revenue": 0, "flow_6m_total": 100})
        self.assertIsNotNone(r)
        self.assertFalse(r.passed)
        self.assertIsNone(r.deviation)


class TestApprovalAuthority(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = CrossChecker()

    def test_审批人授权足够则通过(self):
        r = self.checker.check_approval_authority(
            {"approval_limit": 1000000, "approver_authority": 2000000})
        self.assertTrue(r.passed)

    def test_审批人授权不足则不通过(self):
        r = self.checker.check_approval_authority(
            {"approval_limit": 1000000, "approver_authority": 500000, "loan_amount": 900000})
        self.assertFalse(r.passed)
        self.assertIn("900000", r.detail)

    def test_事实缺失跳过(self):
        self.assertIsNone(self.checker.check_approval_authority({"approval_limit": 100}))


class TestCheckerRun(unittest.TestCase):
    def test_只返回事实齐备的核对项(self):
        checker = CrossChecker()
        results = checker.run({"declared_revenue": 1000000, "flow_6m_total": 900000})
        self.assertEqual([r.check_id for r in results], ["CC-01"])

    def test_阈值非法被拒(self):
        with self.assertRaises(ValueError):
            CrossChecker(deviation_threshold=0)
        with self.assertRaises(ValueError):
            CrossChecker(deviation_threshold=1)

    def test_结果可序列化(self):
        r = CrossChecker().check_revenue_vs_flow({"declared_revenue": 100, "flow_6m_total": 90})
        d = r.to_dict()
        self.assertEqual(d["check_id"], "CC-01")
        self.assertIn("involved_fields", d)


if __name__ == "__main__":
    unittest.main()


class TestZeroAndNaN(unittest.TestCase):
    """评审回归：零营收不得静默跳过；NaN 不得产出确定结论（切片 2 先例）。"""

    def test_零营收非零流水产出冲突(self):
        r = CrossChecker().check_revenue_vs_flow(
            {"declared_revenue": 0, "flow_6m_total": 1000000})
        self.assertIsNotNone(r)
        self.assertFalse(r.passed)
        self.assertIsNone(r.deviation)

    def test_双零视为一致(self):
        r = CrossChecker().check_revenue_vs_flow(
            {"declared_revenue": 0, "flow_6m_total": 0})
        self.assertIsNotNone(r)
        self.assertTrue(r.passed)

    def test_NaN不产出确定结论(self):
        import math
        nan = float("nan")
        self.assertIsNone(CrossChecker().check_revenue_vs_flow(
            {"declared_revenue": nan, "flow_6m_total": 100}))
        self.assertIsNone(CrossChecker().check_revenue_vs_flow(
            {"declared_revenue": 100, "flow_6m_total": nan}))
        self.assertIsNone(CrossChecker().check_approval_authority(
            {"approval_limit": nan, "approver_authority": 100}))
