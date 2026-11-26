"""切片 5 · 判定链审计测量点断言（PRD 12.2 SEC-01 越权召回 = 0）。

评审（切片 3）要求在切片 5 落测量点：SEC-01 要统计"进入判定链次数"，
并记录"谁调用、blocked 集合是什么"。本组断言证明这个测量点是通的，
且**越权召回是真算出来的 0，不是写死的 0**。
"""
from __future__ import annotations

import unittest

from finguard.agents.harness.audit import DecisionChainAuditLog
from finguard.facts.extract import RouteResult
from finguard.facts.layer import FactLayer
from finguard.facts.models import SourceChannel

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


def route(route_name, values, channel=SourceChannel.OCR):
    return RouteResult(route=route_name, channel=channel, values=dict(values))


def build_factset():
    """一路一致、一路不一致：monthly_revenue 为低置信且进入待裁决。"""
    a = dict(GOOD_A)
    b = dict(GOOD_A)
    b["monthly_revenue"] = 615000
    return FactLayer().build(route("vote", a), route("rule_check", b))


class TestDecisionChainAudit(unittest.TestCase):
    def setUp(self):
        self.factset, self.queue = build_factset()
        self.log = DecisionChainAuditLog(clock=lambda: "2026-11-26T10:00:00")

    def test_进入判定链被记录(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        audited.decision_ready()
        self.assertEqual(self.log.entered_count, 1)

    def test_记录含调用方与案件号(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        audited.decision_ready()
        entry = self.log.entries[0]
        self.assertEqual(entry.caller, "审查 Agent")
        self.assertEqual(entry.case_id, "FG-1")

    def test_被拦字段与原因可查(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        audited.decision_ready()
        entry = self.log.entries[0]
        self.assertIn("monthly_revenue", entry.blocked_fields)
        reasons = dict(entry.blocked_reasons)
        self.assertEqual(reasons["monthly_revenue"], "LOW_CONFIDENCE")

    def test_低置信字段未进入判定链(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        ready = audited.decision_ready()
        self.assertNotIn("monthly_revenue", ready)
        self.assertIn("declared_revenue", ready)

    def test_越权召回为零(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        audited.decision_ready()
        self.assertEqual(self.log.violation_count, 0)

    def test_不同调用方分别记录(self):
        first = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        second = self.log.attach(self.factset, caller="风险研判 Agent", case_id="FG-1")
        first.decision_ready()
        second.decision_ready()
        self.assertEqual(self.log.entered_count, 2)
        self.assertEqual(
            [e.caller for e in self.log.entries], ["审查 Agent", "风险研判 Agent"]
        )

    def test_未挂钩子时行为不变(self):
        """不挂审计钩子的事实集照常工作——测量点是可选项，不是强制副作用。"""
        ready = self.factset.decision_ready()
        self.assertIn("declared_revenue", ready)
        self.assertEqual(self.log.entered_count, 0)

    def test_审计记录可序列化(self):
        audited = self.log.attach(self.factset, caller="审查 Agent", case_id="FG-1")
        audited.decision_ready()
        d = self.log.to_dict()
        self.assertEqual(d["entered_count"], 1)
        self.assertEqual(d["violation_count"], 0)
        self.assertEqual(len(d["entries"]), 1)