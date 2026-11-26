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

    def test_申报营收为零时跳过(self):
        self.assertIsNone(self.checker.check_revenue_vs_flow(
            {"declared_revenue": 0, "flow_6m_total": 100}))


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
