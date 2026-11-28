"""切片 6 评审补充断言：回放窗口语义、空窗口取证、改写留痕对账、
存储层隔离与坏值口径。

每条对应评审探针 RP1—RP7 确认的一个缺陷，先红后绿。
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from finguard.agents.harness.gates import Signer
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import Stage
from finguard.agents.replay import ConclusionTrace, ReplayEngine
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
from finguard.storage.event_log import EventLog, EventLogError

CASE_ID = "FG-REVIEW-06"
NOW = "2026-11-26T10:00:00"
FULL_TRAIL = ("S1", "S2", "S3", "GATE_A", "S4", "GATE_B", "S5", "S6")


def route(name, values):
    return RouteResult(route=name, channel=SourceChannel.OCR, values=dict(values))


def signer(role, level, account, fp, ip):
    return Signer(
        account_id=account, display_name=account, role=role,
        authority_level=level, authorized_case_types=("corp_credit_review",),
        device_fingerprint=fp, ip=ip,
    )


SIGNER_A = dict(role="内控合规审查岗", level=2, account="u-001", fp="fp-a", ip="10.0.0.8")
SIGNER_B = dict(role="风险研判岗", level=3, account="u-002", fp="fp-b", ip="10.0.0.9")


def run_case(with_amendment=False):
    """受理 → 审查意见（可含人工改写）→ GATE-A → 研判 → GATE-B → 归档。"""
    log = EventLog(clock=lambda: NOW)
    case = CaseFile(
        case_id=CASE_ID,
        triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
    )
    h = Harness(case, event_sink=log.append_sink(), clock=lambda: NOW)
    h.auto_advance()
    opinion = ReviewOpinion(
        opinion_id="OP-%s-001" % CASE_ID, rule_id="R-CR-013", rule_version="3.2.0",
        severity=Severity.MAJOR, fact_refs=("monthly_revenue",),
        rationale="流水与申报营收偏离 90%", coverage=CoverageFlag.JUDGED,
        quotes=("《信贷业务审查操作规程》第四条",),
    )
    h.record_opinion(opinion)
    if with_amendment:
        h.record_amendment(
            "GATE-A", rule_id="R-CR-013", rule_version="3.2.0",
            original_rationale="原始意见", amended_rationale="修改后意见",
            reason="补充政策依据", amended_by="u-001",
        )
    h.sign_gate("GATE-A", signer=signer(**SIGNER_A))
    h.transition(Stage.S4, actor="编排器")
    h.auto_advance()
    h.sign_gate("GATE-B", signer=signer(**SIGNER_B))
    h.transition(Stage.S5, actor="编排器")
    h.record_disposition("调整关注", reason="流水与申报偏离")
    h.transition(Stage.S6, actor="编排器")
    return log, case, h, opinion


def build_dossier(case, h, opinion):
    a = {"monthly_revenue": 820000, "declared_revenue": 1000000}
    factset, _q = FactLayer().build(route("vote", a), route("rule_check", a))
    trail = [e["detail"]["to_stage"] for e in h.events if e["kind"] == "stage_transition"]
    d = Dossier(case.case_id, clock=lambda: NOW)
    d.write(DossierLayer.L1, facts_layer_payload(factset))
    d.write(DossierLayer.L2, rules_layer_payload(case.rule_binding, case.unresolved))
    d.write(DossierLayer.L3, documents_layer_payload(
        [{"doc_id": "D-1", "chunk_id": "C-1", "source_ref": "《操作规程》第四条",
          "quote": "原文片段", "published_at": "2025-07-01"}]))
    d.write(DossierLayer.L4, opinions_layer_payload(
        [opinion], {"judged": 1, "not_applicable": 0, "unresolved": 0, "total": 1},
        spec_version="auditor-1.0"))
    d.write(DossierLayer.L5, signatures_layer_payload(case.gates, trail))
    d.write(DossierLayer.L6, disposition_layer_payload({"action": "调整关注", "by": "u-002"}))
    return d


class _CaseFixture(unittest.TestCase):
    def setUp(self):
        self.log, self.case, self.harness, self.opinion = run_case()
        self.dossier = build_dossier(self.case, self.harness, self.opinion)
        self.engine = ReplayEngine(self.log, self.dossier)


class TestVacuousReplayNotUsable(_CaseFixture):
    """RP1/RP3/RP7：取证判定不得在空集合上恒真。"""

    def test_越界起点空窗口不可取证(self):
        result = self.engine.replay(CASE_ID, from_seq=999)
        self.assertEqual(len(result.events), 0)
        self.assertFalse(result.usable_for_audit)

    def test_起止倒挂空窗口不可取证(self):
        result = self.engine.replay(CASE_ID, from_seq=999, to_seq=1000)
        self.assertFalse(result.usable_for_audit)

    def test_窗口无结论内容不可取证(self):
        # 找到 disposition 事件的 seq，单独重放这一条：有事件但重建不出任何结论
        disp = [e for e in self.log.events(CASE_ID) if e.kind == "disposition"][0]
        result = self.engine.replay(CASE_ID, from_seq=disp.seq, to_seq=disp.seq)
        self.assertEqual(len(result.events), 1)
        self.assertFalse(result.usable_for_audit)

    def test_案件与卷宗不配对直接拒绝(self):
        # 引擎绑定的卷宗属于本案，重放他案是调用方配对错误——必须抛，
        # 而不是拿 A 案卷宗给 B 案出「可取证」结论
        with self.assertRaises(ValueError):
            self.engine.replay("FG-别的案件")


class TestPartialWindowReplay(_CaseFixture):
    """RP2：PRD 8.9「指定起始 seq 重放至任意节点」——前缀 / 中间窗口必须可用。"""

    def test_前缀窗口重放到中间节点对账一致(self):
        gate_a_signed = [e for e in self.log.events(CASE_ID)
                         if e.kind == "gate_signed"][0]
        result = self.engine.replay(CASE_ID, from_seq=1, to_seq=gate_a_signed.seq)
        self.assertTrue(result.matches_dossier)
        self.assertTrue(result.usable_for_audit)
        self.assertEqual(result.trace.stages, ("S1", "S2", "S3", "GATE_A"))
        self.assertEqual(result.trace.final_stage, "GATE_A")

    def test_中间窗口重放对账一致(self):
        events = self.log.events(CASE_ID)
        lo = events[4].seq
        hi = events[8].seq
        result = self.engine.replay(CASE_ID, from_seq=lo, to_seq=hi)
        self.assertTrue(result.matches_dossier)
        self.assertTrue(result.usable_for_audit)
        # 窗口轨迹必须是完整轨迹的连续子段
        full = ReplayEngine.rebuild_trace(events)
        windowed = result.trace
        self.assertIn(windowed.stages[0], full.stages)
        self.assertTrue(len(windowed.stages) <= len(full.stages))

    def test_全程重放仍然一致(self):
        result = self.engine.replay(CASE_ID)
        self.assertEqual(result.trace.stages, FULL_TRAIL)
        self.assertTrue(result.matches_dossier)
        self.assertTrue(result.usable_for_audit)


class TestAmendmentReconciliation(unittest.TestCase):
    """RP4：FR-GATE-06 双版本——合法覆盖写丢掉改写留痕也必须被对账检出。"""

    def test_改写留痕进入重放轨迹且对账一致(self):
        log, case, h, opinion = run_case(with_amendment=True)
        dossier = build_dossier(case, h, opinion)
        engine = ReplayEngine(log, dossier)
        result = engine.replay(CASE_ID)
        self.assertEqual(result.trace.amendments, (("GATE-A", "R-CR-013", "3.2.0"),))
        self.assertTrue(result.matches_dossier)
        self.assertTrue(result.usable_for_audit)

    def test_卷宗丢掉改写留痕则对账不一致(self):
        log, case, h, opinion = run_case(with_amendment=True)
        dossier = build_dossier(case, h, opinion)
        engine = ReplayEngine(log, dossier)
        # 归档 Agent「重新组装」L5，amendments 掉了——走的是合法 write 口，
        # 内容哈希对新 payload 自洽，只有事件对账能发现双版本消失
        trail = [e["detail"]["to_stage"] for e in h.events
                 if e["kind"] == "stage_transition"]
        stripped = {gid: rec.to_dict() for gid, rec in case.gates.items()}
        for g in stripped.values():
            g["amendments"] = []
        dossier.write(DossierLayer.L5, signatures_layer_payload(stripped, trail))
        result = engine.replay(CASE_ID)
        self.assertFalse(result.matches_dossier)
        self.assertFalse(result.usable_for_audit)

    def test_轨迹序列化含改写留痕(self):
        trace = ConclusionTrace(
            stages=("S1",), amendments=(("GATE-A", "R-1", "1.0.0"),), final_stage="S1")
        restored = ConclusionTrace.from_dict(trace.to_dict())
        self.assertEqual(restored.amendments, (("GATE-A", "R-1", "1.0.0"),))


class TestStorageIsolation(unittest.TestCase):
    """RP5/RP6：留痕与调用方内存隔离；坏值走统一异常口径。"""

    def test_追加后调用方改动自己的dict不破坏链(self):
        log = EventLog(clock=lambda: NOW)
        payload = {"detail": {"to_stage": "S1"}, "tags": ["a"]}
        log.append(CASE_ID, "stage_transition", payload)
        payload["detail"]["to_stage"] = "S9"      # 调用方复用/清洗自己的 dict
        payload["tags"].append("b")
        self.assertTrue(log.verify().ok)          # 日志已深拷贝，不受影响

    def test_卷宗写入后调用方改动自己的dict不破坏层(self):
        d = Dossier(CASE_ID, clock=lambda: NOW)
        payload = {"fields": [{"field_path": "a", "value": 1}]}
        d.write(DossierLayer.L1, payload)
        payload["fields"][0]["value"] = 999
        self.assertTrue(d.read(DossierLayer.L1).verify_self())

    def test_不可序列化payload归入EventLogError(self):
        log = EventLog(clock=lambda: NOW)
        with self.assertRaises(EventLogError):
            log.append(CASE_ID, "weird", {"when": datetime(2026, 11, 26)})

    def test_payload非映射被拒(self):
        log = EventLog(clock=lambda: NOW)
        with self.assertRaises(EventLogError):
            log.append(CASE_ID, "weird", payload=[("a", 1)])

    def test_空层内容拒写(self):
        d = Dossier(CASE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            d.write(DossierLayer.L3, {})
        self.assertFalse(d.has(DossierLayer.L3))

    def test_非法层仍先拒(self):
        d = Dossier(CASE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            d.write("L7", {})


if __name__ == "__main__":
    unittest.main()
