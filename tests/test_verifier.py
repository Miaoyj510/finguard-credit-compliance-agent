"""切片 7 · 核查 Agent 断言（PRD 6.4 FR-POST-06）。"""
from __future__ import annotations

import unittest

from finguard.agents.verifier import VerifierAgent
from finguard.domain.post import (
    Alert,
    AlertLevel,
    VerificationOutcome,
    freeze_snapshot,
)


def make_alert(evidence=None, level=AlertLevel.HIGH):
    snap = evidence if evidence is not None else {
        "facts": {
            "days_past_due": {"value": 45, "channel": "api", "confidence": "高"},
        },
        "rule": {"rule_id": "R-PM-001", "version": "1.0.0"},
    }
    return Alert(
        alert_id="AL-FG-1-R-PM-001",
        case_id="FG-1",
        rule_id="R-PM-001",
        rule_version="1.0.0",
        hit_at="2026-03-01T09:00:00",
        baseline_date="2026-01-15",
        lead_time_days=45,
        level=level,
        evidence=freeze_snapshot(snap),
    )


class TestVerificationOutcome(unittest.TestCase):
    def setUp(self):
        self.verifier = VerifierAgent(clock=lambda: "2026-03-02T10:00:00")

    def test_证据齐备且一致判证据支持(self):
        result = self.verifier.verify(make_alert(), {"days_past_due": 45})
        self.assertIs(result.outcome, VerificationOutcome.CONFIRMED)
        self.assertEqual(result.supplemented_fields, ("days_past_due",))
        self.assertEqual(result.evidence_gaps, ())

    def test_取值已变判证据不支持(self):
        result = self.verifier.verify(make_alert(), {"days_past_due": 0})
        self.assertIs(result.outcome, VerificationOutcome.UNCONFIRMED)
        self.assertIn("days_past_due", result.rationale)

    def test_取不到字段判证据不足(self):
        result = self.verifier.verify(make_alert(), {})
        self.assertIs(result.outcome, VerificationOutcome.INSUFFICIENT)
        self.assertEqual(result.evidence_gaps, ("days_past_due",))
        self.assertTrue(result.recommend_escalation)

    def test_布尔与数字不算一致(self):
        alert = make_alert(evidence={
            "facts": {"flag": {"value": False}},
        })
        result = self.verifier.verify(alert, {"flag": 0})
        self.assertIs(result.outcome, VerificationOutcome.UNCONFIRMED)

    def test_一致且低等级不建议升级(self):
        alert = make_alert(level=AlertLevel.LOW)
        result = self.verifier.verify(alert, {"days_past_due": 45})
        self.assertFalse(result.recommend_escalation)

    def test_证据不支持且等级高建议升级(self):
        result = self.verifier.verify(make_alert(), {"days_past_due": 0})
        self.assertTrue(result.recommend_escalation)

    def test_核查记录可序列化(self):
        d = self.verifier.verify(make_alert(), {"days_past_due": 45}).to_dict()
        self.assertEqual(d["outcome"], "证据支持")
        self.assertEqual(d["alert_id"], "AL-FG-1-R-PM-001")


class TestUnverifiedReason(unittest.TestCase):
    """FR-POST-06：每条预警要么有核查记录，要么有明确的「未核查」及原因。"""

    def setUp(self):
        self.verifier = VerifierAgent(clock=lambda: "2026-03-02T10:00:00")

    def test_未核查必须给原因码(self):
        with self.assertRaises(ValueError):
            self.verifier.mark_unverified(make_alert(), "  ", "缺材料")

    def test_未核查必须给原因说明(self):
        with self.assertRaises(ValueError):
            self.verifier.mark_unverified(make_alert(), "NO_STAFF", "  ")

    def test_未核查记录完整(self):
        reason = self.verifier.mark_unverified(make_alert(), "NO_STAFF", "月末人力不足")
        self.assertEqual(reason.reason_code, "NO_STAFF")
        self.assertEqual(reason.detail, "月末人力不足")
        self.assertEqual(reason.alert_id, "AL-FG-1-R-PM-001")