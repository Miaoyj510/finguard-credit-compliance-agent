"""切片 5 · Handoff 契约断言（PRD 8.6 Handoff 必填字段表）。

核心主张：**缺必填字段即中断交接，不允许用默认值补齐。**
"""
from __future__ import annotations

import unittest

from finguard.agents.harness.handoff import (
    AGENT_ROLES,
    HANDOFF_REQUIRED_FIELDS,
    Handoff,
)
from finguard.agents.harness.states import HandoffIncompleteError, Stage


def good_handoff(**overrides):
    data = dict(
        handoff_id="HO-FG-1-1",
        case_id="FG-20261126-000001",
        from_agent="审查 Agent",
        to_agent="风险研判 Agent",
        stage=Stage.S3,
        rule_binding=({"rule_id": "R-CR-013", "version": "3.2.0",
                       "binding_basis": "三键唯一命中"},),
        facts_digest=({"field_path": "declared_revenue", "value": 820000,
                       "confidence": "高", "conflict_state": "无"},),
        citation_bundle=(),
        open_questions=(),
        coverage_summary={"judged": 41, "not_applicable": 9,
                          "unresolved": 2, "total": 52},
        human_gate_state={"gate_id": "GATE-A", "state": "PENDING",
                          "has_signature": False},
        produced_at="2026-11-26T10:00:00",
        producer_spec_version="auditor-1.0",
        severity_source="rule_store",
    )
    data.update(overrides)
    return Handoff(**data)


class TestHandoffRequiredFields(unittest.TestCase):
    def test_必填字段共14个(self):
        self.assertEqual(len(HANDOFF_REQUIRED_FIELDS), 14)

    def test_空交接缺全部必填字段(self):
        self.assertEqual(
            set(Handoff().missing_fields()), set(HANDOFF_REQUIRED_FIELDS)
        )

    def test_缺字段即中断(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(producer_spec_version=None).validate()

    def test_事实摘要为空视为缺失(self):
        h = good_handoff(facts_digest=())
        self.assertIn("facts_digest", h.missing_fields())
        with self.assertRaises(HandoffIncompleteError):
            h.validate()

    def test_依据出处与待裁决项允许为空数组(self):
        """「确实没有」与「没有传」必须能区分：空数组是合法表达。"""
        h = good_handoff(citation_bundle=(), open_questions=())
        h.validate()          # 不抛异常即通过

    def test_完整交接通过校验(self):
        good_handoff().validate()

    def test_严重度来源非规则库被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(severity_source="model").validate()

    def test_交接边不在拓扑中被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(from_agent="审查 Agent", to_agent="核查 Agent").validate()

    def test_未知角色被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(from_agent="神秘 Agent").validate()

    def test_五个角色齐备(self):
        self.assertEqual(len(AGENT_ROLES), 5)

    def test_覆盖度三类之和不等于总数被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(
                coverage_summary={"judged": 41, "not_applicable": 9,
                                  "unresolved": 2, "total": 51}
            ).validate()

    def test_未判定规则缺原因码被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(
                rule_binding=({"rule_id": "R-CR-021", "version": "1.4.1",
                               "binding_basis": "须人工判定"},)
            ).validate()

    def test_事实摘要缺字段被拒(self):
        with self.assertRaises(HandoffIncompleteError):
            good_handoff(
                facts_digest=({"field_path": "declared_revenue", "value": 1},)
            ).validate()

    def test_交接包可序列化且带缺失清单(self):
        d = good_handoff().to_dict()
        self.assertEqual(d["case_id"], "FG-20261126-000001")
        self.assertEqual(d["missing_fields"], [])
        self.assertEqual(d["stage"], "S3")