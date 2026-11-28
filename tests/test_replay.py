"""切片 6 · 案件回放断言（PRD 8.9 回放规格、8.10、F-09 / F-13）。

本组是切片 6 的主战场：**回放必须与当时的留痕对得上**，
且**不完整 / 链断 / 对不上**的卷宗一律不可用于监管取证。
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.harness.gates import Signer
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import Stage
from finguard.agents.replay import ReplayEngine
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile
from finguard.domain.enums import CoverageFlag, Severity
from finguard.domain.opinion import ReviewOpinion
from finguard.facts.extract import RouteResult
from finguard.facts.layer import FactLayer
from finguard.facts.models import SourceChannel
from finguard.storage.dossier import (
    Dossier,
    DossierLayer,
    disposition_layer_payload,
    documents_layer_payload,
    facts_layer_payload,
    opinions_layer_payload,
    rules_layer_payload,
    signatures_layer_payload,
)
from finguard.storage.event_log import EventLog

CASE_ID = "FG-20261126-000001"
NOW = "2026-11-26T10:00:00"
EXPECTED_TRAIL = ("S1", "S2", "S3", "GATE_A", "S4", "GATE_B", "S5", "S6")


def route(name, values):
    return RouteResult(route=name, channel=SourceChannel.OCR, values=dict(values))


def signer_a():
    return Signer(
        account_id="u-001", display_name="李合规", role="内控合规审查岗",
        authority_level=2, authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-a", ip="10.0.0.8",
    )


def signer_b():
    return Signer(
        account_id="u-002", display_name="王研判", role="风险研判岗",
        authority_level=3, authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-b", ip="10.0.0.9",
    )


def make_opinion():
    return ReviewOpinion(
        opinion_id="OP-%s-001" % CASE_ID,
        rule_id="R-CR-013",
        rule_version="3.2.0",
        severity=Severity.MAJOR,
        fact_refs=("monthly_revenue",),
        rationale="流水与申报营收偏离 90%",
        coverage=CoverageFlag.JUDGED,
        quotes=("《信贷业务审查操作规程》第四条",),
    )


def run_case():
    """跑完一条完整链路（受理 → 审查 → GATE-A → 研判 → GATE-B → 归档）。"""
    log = EventLog(clock=lambda: NOW)
    case = CaseFile(
        case_id=CASE_ID,
        triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
    )
    harness = Harness(case, event_sink=log.append_sink(), clock=lambda: NOW)

    harness.auto_advance()                      # S1 → S2 → S3 → GATE_A
    opinion = make_opinion()
    harness.record_opinion(opinion)
    harness.sign_gate("GATE-A", signer=signer_a())
    harness.transition(Stage.S4, actor="编排器")
    harness.auto_advance()                      # S4 → GATE_B
    harness.sign_gate("GATE-B", signer=signer_b())
    harness.transition(Stage.S5, actor="编排器")
    harness.record_disposition("调整关注", reason="流水与申报偏离")
    harness.transition(Stage.S6, actor="编排器")
    return log, case, harness, opinion


def build_dossier(case, harness, opinion) -> Dossier:
    """按 PRD 8.9 的六层组装卷宗。"""
    a = {"monthly_revenue": 820000, "declared_revenue": 1000000}
    factset, _queue = FactLayer().build(route("vote", a), route("rule_check", a))
    trail = [
        e["detail"]["to_stage"]
        for e in harness.events
        if e["kind"] == "stage_transition"
    ]
    dossier = Dossier(case.case_id, clock=lambda: NOW)
    dossier.write(DossierLayer.L1, facts_layer_payload(factset))
    dossier.write(DossierLayer.L2, rules_layer_payload(case.rule_binding, case.unresolved))
    dossier.write(
        DossierLayer.L3,
        documents_layer_payload(
            [{"doc_id": "D-1", "chunk_id": "C-1", "source_ref": "《信贷业务审查操作规程》第四条",
              "quote": "原文片段", "published_at": "2025-07-01"}]
        ),
    )
    dossier.write(
        DossierLayer.L4,
        opinions_layer_payload([opinion], {"judged": 1, "not_applicable": 0, "unresolved": 0,
                                           "total": 1}, spec_version="auditor-1.0"),
    )
    dossier.write(DossierLayer.L5, signatures_layer_payload(case.gates, trail))
    dossier.write(
        DossierLayer.L6,
        disposition_layer_payload({"action": "调整关注", "by": "u-002"}),
    )
    return dossier


class TestReplayConsistency(unittest.TestCase):
    def setUp(self):
        self.log, self.case, self.harness, self.opinion = run_case()
        self.dossier = build_dossier(self.case, self.harness, self.opinion)
        self.engine = ReplayEngine(self.log, self.dossier)

    def test_重放重建出阶段轨迹(self):
        result = self.engine.replay(CASE_ID)
        self.assertEqual(result.trace.stages, EXPECTED_TRAIL)

    def test_重放重建出两处签核(self):
        result = self.engine.replay(CASE_ID)
        self.assertEqual(
            result.trace.signatures,
            (("GATE-A", "u-001", NOW), ("GATE-B", "u-002", NOW)),
        )

    def test_重放重建出意见编号(self):
        result = self.engine.replay(CASE_ID)
        self.assertEqual(result.trace.opinion_ids, ("OP-%s-001" % CASE_ID,))

    def test_同案同起点重放结论一致(self):
        first = self.engine.replay(CASE_ID, from_seq=1)
        second = self.engine.replay(CASE_ID, from_seq=1)
        self.assertEqual(first.trace, second.trace)
        self.assertEqual(first.to_dict()["trace"], second.to_dict()["trace"])

    def test_重放与卷宗对账一致(self):
        result = self.engine.replay(CASE_ID)
        self.assertTrue(result.matches_dossier)
        self.assertTrue(result.usable_for_audit)

    def test_从中间起点重放与卷宗后缀一致(self):
        result = self.engine.replay(CASE_ID, from_seq=4)
        self.assertTrue(result.matches_dossier)
        self.assertEqual(result.trace.final_stage, "S6")

    def test_链完整(self):
        self.assertTrue(self.log.verify().ok)
        self.assertTrue(self.engine.replay(CASE_ID).chain_ok)

    def test_意见可按规则版本归因(self):
        buckets = self.engine.attribution(CASE_ID)
        self.assertIn("R-CR-013@3.2.0", buckets)
        self.assertEqual(buckets["R-CR-013@3.2.0"], ["OP-%s-001" % CASE_ID])


class TestReplayRefusesIncomplete(unittest.TestCase):
    """不完整 / 链断 / 对不上——三种情况一律**不可用于监管取证**。"""

    def setUp(self):
        self.log, self.case, self.harness, self.opinion = run_case()
        self.dossier = build_dossier(self.case, self.harness, self.opinion)

    def test_缺任一层判为不完整且不可取证(self):
        self.dossier._layers.pop(DossierLayer.L3)
        result = ReplayEngine(self.log, self.dossier).replay(CASE_ID)
        self.assertFalse(result.usable_for_audit)
        self.assertIn("L3_依据文档快照", result.missing_layers)

    def test_卷宗被改过则轨迹对不上(self):
        snapshot = self.dossier.read(DossierLayer.L5)
        snapshot.payload["stage_trail"] = ["S1", "S2"]
        result = ReplayEngine(self.log, self.dossier).replay(CASE_ID)
        self.assertFalse(result.matches_dossier)
        self.assertFalse(result.usable_for_audit)

    def test_事件被篡改则链校验失败(self):
        events = list(self.log.events(CASE_ID))
        events[1].payload["to_stage"] = "S9"      # 白盒模拟篡改
        result = ReplayEngine(self.log, self.dossier).replay(CASE_ID)
        self.assertFalse(result.chain_ok)
        self.assertFalse(result.usable_for_audit)
        self.assertIn("内容", result.chain_reason)

    def test_缺签核层的案件不可取证(self):
        self.dossier._layers.pop(DossierLayer.L5)
        result = ReplayEngine(self.log, self.dossier).replay(CASE_ID)
        self.assertFalse(result.usable_for_audit)

    def test_完整卷宗可取证(self):
        result = ReplayEngine(self.log, self.dossier).replay(CASE_ID)
        self.assertTrue(result.usable_for_audit)
        self.assertEqual(result.to_dict()["completeness"], "完整")