"""切片 6 · 评审咨询 C2 / C3 与评审建议 3 / 8 的落地断言。

    C2  跨轮次意见编号撞号 -> 卷宗键静默覆盖（切片 6 契约）
    C3  改写留痕是"自觉登记" -> 卷宗组装时强校验（切片 6 契约）
    建议 3  重放算出的 stage 必须与案件当前阶段对账
    建议 8  重放轨迹必须满足状态机拓扑，且状态机无绕行边
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.auditor import Auditor
from finguard.agents.replay import ConclusionTrace, ReplayEngine
from finguard.domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from finguard.domain.opinion import OpinionOrigin, ReviewOpinion
from finguard.domain.rule import Rule
from finguard.facts.models import Confidence, FactField, FactSet, SourceChannel
from finguard.rules.engine import Evaluation
from finguard.storage.dossier import (
    Dossier,
    DossierError,
    DossierLayer,
    opinions_layer_payload,
)

CASE_ID = "FG-20261126-000001"

RULE = Rule(
    rule_id="R-CR-013", version="3.2.0",
    effective_from=date(2025, 7, 1), effective_to=None,
    product_scope=("小额经营贷",), org_scope=("*",),
    severity=Severity.MAJOR,
    dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('申报营收与流水不符')",
    required_facts=("monthly_revenue", "declared_revenue"),
    source_ref="《信贷业务审查操作规程》第四条（演示用合成条款）",
)
RULES = {"R-CR-013@3.2.0": RULE}


def hit_eval():
    return Evaluation(
        rule_id="R-CR-013", rule_version="3.2.0",
        verdict=Verdict.HIT, coverage=CoverageFlag.JUDGED,
        reason="命中", reason_code=None,
        fact_refs=("monthly_revenue", "declared_revenue"),
        values=(("monthly_revenue", 820000), ("declared_revenue", 1000000)),
        severity=Severity.MAJOR, hit_label="申报营收与流水不符",
    )


def factset():
    return FactSet(fields={
        "monthly_revenue": FactField("monthly_revenue", 820000, Confidence.HIGH,
                                     channels=(SourceChannel.OCR,)),
        "declared_revenue": FactField("declared_revenue", 1000000, Confidence.HIGH,
                                      channels=(SourceChannel.API,)),
    })


def build_round(start_seq):
    return Auditor().build([hit_eval()], factset(), RULES,
                            case_id=CASE_ID, start_seq=start_seq)


class TestC2OpinionNumbering(unittest.TestCase):
    """C2：同一案件两轮成稿，意见编号不得撞号。"""

    def test_两轮成稿编号不撞(self):
        first = build_round(start_seq=0)
        second = build_round(start_seq=10)
        self.assertNotEqual(first.opinions[0].opinion_id, second.opinions[0].opinion_id)

    def test_编号含start_seq基准(self):
        second = build_round(start_seq=10)
        self.assertEqual(second.opinions[0].opinion_id, "OP-%s-011" % CASE_ID)

    def test_默认基准仍从001起(self):
        first = build_round(start_seq=0)
        self.assertEqual(first.opinions[0].opinion_id, "OP-%s-001" % CASE_ID)

    def test_卷宗键带修订维度两轮不覆盖(self):
        first = build_round(start_seq=0)
        second = build_round(start_seq=10)
        p1 = opinions_layer_payload(first.opinions, revision=0)
        p2 = opinions_layer_payload(second.opinions, revision=1)
        key1 = p1["opinions"][0]["opinion_key"]
        key2 = p2["opinions"][0]["opinion_key"]
        self.assertNotEqual(key1, key2)
        self.assertTrue(key1.endswith("@r0"))
        self.assertTrue(key2.endswith("@r1"))


class TestC3AmendmentConsistency(unittest.TestCase):
    """C3：人工改写的意见必须带 amendment 记录，否则卷宗不得组装。"""

    def _amended_opinion(self):
        return ReviewOpinion(
            opinion_id="OP-%s-001" % CASE_ID,
            rule_id="R-CR-013", rule_version="3.2.0",
            severity=Severity.MAJOR,
            fact_refs=("monthly_revenue",),
            rationale="修改后的理由",
            coverage=CoverageFlag.JUDGED,
            origin=OpinionOrigin.HUMAN_AMENDED,
        )

    def _amendment(self):
        return {
            "rule_id": "R-CR-013", "rule_version": "3.2.0",
            "original_rationale": "原始理由", "amended_rationale": "修改后的理由",
            "reason": "补充依据", "amended_by": "u-001", "amended_at": "2026-11-26T10:00:00",
        }

    def test_改写缺留痕则组装失败(self):
        with self.assertRaises(DossierError):
            opinions_layer_payload([self._amended_opinion()])

    def test_改写有留痕则组装通过(self):
        payload = opinions_layer_payload(
            [self._amended_opinion()], amendments=[self._amendment()]
        )
        self.assertEqual(payload["amendment_count"], 1)

    def test_普通意见不受影响(self):
        normal = ReviewOpinion(
            opinion_id="OP-%s-002" % CASE_ID,
            rule_id="R-CR-013", rule_version="3.2.0",
            severity=Severity.MAJOR, fact_refs=("monthly_revenue",),
            rationale="正常理由", coverage=CoverageFlag.JUDGED,
            origin=OpinionOrigin.MODEL,
        )
        payload = opinions_layer_payload([normal])
        self.assertEqual(len(payload["opinions"]), 1)


class TestReplayCrossChecks(unittest.TestCase):
    """评审建议 3 / 8：回放要多做两项对账，才谈得上"可用于取证"。"""

    def _engine(self, trace_stages):
        """造一个只有 L4 层的卷宗 + 一条手工轨迹，用来单独验对账逻辑。"""
        dossier = Dossier("FG-X")
        for layer in DossierLayer:
            dossier.write(layer, {"占位": layer.value})
        return ReplayEngine(_FakeLog(), dossier), trace_stages

    def test_阶段与案件态一致时通过(self):
        engine, _ = self._engine([])
        result = engine.replay("FG-X", expected_stage="S6")
        self.assertEqual(result.trace.final_stage, "S6")
        self.assertTrue(result.stage_consistent)

    def test_阶段与案件态不一致时不可取证(self):
        engine, _ = self._engine([])
        result = engine.replay("FG-X", expected_stage="S9")
        self.assertFalse(result.stage_consistent)
        self.assertFalse(result.usable_for_audit)

    def test_非法拓扑轨迹被判不通过(self):
        """S3 直接跳 S4 是绕行边，轨迹里出现就必须被 topo 自检抓住。"""
        engine, _ = self._engine([])
        ok = engine.topology_ok(ConclusionTrace(stages=("S1", "S2", "S3", "S4")))
        self.assertFalse(ok)

    def test_合法轨迹通过拓扑自检(self):
        engine, _ = self._engine([])
        ok = engine.topology_ok(
            ConclusionTrace(stages=("S1", "S2", "S3", "GATE_A", "S4", "GATE_B", "S5", "S6"))
        )
        self.assertTrue(ok)


class TestTimestampUnification(unittest.TestCase):
    """C2：事件时间戳与签核时间戳必须**同一口径**（naive UTC）。

    评审方指出：naive 时钟下两者恒等（看不出问题），
    **带时区时钟下会失配**——签核对账判不一致，回放报"当时与现在对不上"。
    """

    def test_带时区归一为naiveUTC(self):
        from finguard.agents.harness.timeutil import normalize_timestamp
        self.assertEqual(
            normalize_timestamp("2026-11-26T10:00:00+08:00"), "2026-11-26T02:00:00"
        )

    def test_归一是幂等的(self):
        from finguard.agents.harness.timeutil import normalize_timestamp
        once = normalize_timestamp("2026-11-26T10:00:00+08:00")
        self.assertEqual(normalize_timestamp(once), once)

    def test_坏值被拒(self):
        from finguard.agents.harness.timeutil import TimestampError, normalize_timestamp
        for bad in ("昨天", "", 12345, None):
            with self.assertRaises(TimestampError):
                normalize_timestamp(bad)

    def test_带时区时钟下事件与签核时间戳一致(self):
        """这是 C2 的核心断言：两种写法、同一时刻、同一口径。"""
        from datetime import date
        from finguard.agents.harness.gates import Signer
        from finguard.agents.harness.orchestrator import Harness
        from finguard.domain.case import TripleKey
        from finguard.domain.case_state import CaseFile
        from finguard.agents.harness.states import Stage

        stamp = "2026-11-26T10:00:00+08:00"
        case = CaseFile(
            case_id="FG-TS-000001",
            triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
        )
        harness = Harness(case, clock=lambda: stamp)
        harness.transition(Stage.S1, actor="编排器")
        harness.transition(Stage.S2, actor="编排器")
        harness.transition(Stage.S3, actor="编排器")
        harness.transition(Stage.GATE_A, actor="编排器")
        harness.sign_gate("GATE-A", signer=Signer(
            account_id="u-a", display_name="李合规", role="内控合规审查岗",
            authority_level=2, authorized_case_types=("corp_credit_review",),
            device_fingerprint="fp-a", ip="10.0.0.8"))
        signed_at = case.gates["GATE-A"].signed_at
        event_at = [e["at"] for e in harness.events if e["kind"] == "gate_signed"][0]
        self.assertEqual(signed_at, event_at)
        self.assertNotIn("+08:00", event_at)
        self.assertNotIn("+", signed_at)


class _FakeLog:
    """极简日志替身：按给定阶段序列造出可回放的事件。"""

    def __init__(self, stages=("S1", "S2", "S3", "GATE_A", "S4", "GATE_B", "S5", "S6")):
        from finguard.storage.event_log import Event, compute_hash
        self._events = []
        prev = "0" * 64
        for i, stage in enumerate(stages, start=1):
            payload = {"to_stage": stage}
            digest = compute_hash(i, "FG-X", "stage_transition", "2026-11-26T10:00:00",
                                  payload, prev)
            self._events.append(Event(
                seq=i, case_id="FG-X", kind="stage_transition",
                at="2026-11-26T10:00:00", payload=payload,
                prev_hash=prev, hash=digest,
            ))
            prev = digest

    def events(self, case_id=None):
        return tuple(self._events)

    def since(self, case_id, seq):
        return tuple(e for e in self._events if e.seq >= seq)

    def verify(self):
        from finguard.storage.event_log import ChainVerification
        return ChainVerification(True, None, "")