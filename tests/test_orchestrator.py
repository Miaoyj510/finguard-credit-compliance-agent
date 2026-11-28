"""切片 5 · 编排器断言（PRD 8.2 编排拓扑、8.7 闸门、12.2 SEC-03 闸门绕行 = 0）。

本组是切片 5 的核心：**专测"试图绕过闸门的路径全部失败"**，
而不是只测"正常流程能走通"。正常路径只保留一条，用来说明闸门放行后能继续。
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.harness.gates import GateState, Signer
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import (
    GateBypassError,
    GateSignError,
    HandoffIncompleteError,
    Stage,
    StageMutationError,
    TransitionError,
)
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile
from finguard.domain.opinion import CoverageSummary
from finguard.rules.registry import RuleBinding

NOW = "2026-11-26T10:00:00"
LATE = "2026-11-28T10:00:00"


def make_signer(**overrides):
    data = dict(
        account_id="u-001",
        display_name="李合规",
        role="内控合规审查岗",
        authority_level=2,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-abc",
        ip="10.0.0.8",
    )
    data.update(overrides)
    return Signer(**data)


def make_harness(now=NOW):
    case = CaseFile(
        case_id="FG-20261126-000001",
        triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
    )
    return Harness(case, clock=lambda: now)


class TestGateCannotBeBypassed(unittest.TestCase):
    """这一组是切片 5 的主战场：每一条都是"绕过尝试必须失败"。"""

    def test_自动推进停在闸门不穿越(self):
        h = make_harness()
        self.assertIs(h.auto_advance(), Stage.GATE_A)
        self.assertIs(h.stage, Stage.GATE_A)
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)

    def test_跳过闸门直接推进到放行后继状态被拒(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateBypassError):
            h.transition(Stage.S4, actor="编排器")
        counters = h.security_counters()
        self.assertEqual(counters["bypass_attempts"], 1)
        self.assertEqual(counters["bypass_succeeded"], 0)
        self.assertIs(h.stage, Stage.GATE_A)

    def test_闸门待签核时不得离开闸门(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(TransitionError):
            h.transition(Stage.S3, actor="编排器")
        self.assertIs(h.stage, Stage.GATE_A)

    def test_直接改写阶段字段被拒(self):
        h = make_harness()
        with self.assertRaises(StageMutationError):
            h.case.stage = Stage.S4
        with self.assertRaises(StageMutationError):
            h.case.set_stage(Stage.S4)
        self.assertIs(h.stage, Stage.S0)

    def test_案件对象上的签核入口被拒(self):
        h = make_harness()
        with self.assertRaises(GateSignError):
            h.case.sign(make_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.NOT_REACHED)

    def test_伪造已签核状态仍拿不到放行资格(self):
        """绕过方法直接改状态字段——没有签核证据照样不放行。"""
        h = make_harness()
        h.auto_advance()
        h.case.gates["GATE-A"]._state = GateState.APPROVED
        with self.assertRaises(GateBypassError):
            h.transition(Stage.S4, actor="编排器")
        self.assertIs(h.stage, Stage.GATE_A)

    def test_超时升级后仍不放行(self):
        h = make_harness()
        h.auto_advance()
        escalations = h.escalate_overdue(LATE)
        self.assertEqual(len(escalations), 1)
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)
        with self.assertRaises(GateBypassError):
            h.transition(Stage.S4, actor="编排器")
        self.assertIs(h.stage, Stage.GATE_A)

    def test_无资格账号签核被拒且闸门状态不变(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=make_signer(role="客户经理"))
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)

    def test_不在闸门阶段签核被拒(self):
        h = make_harness()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=make_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.NOT_REACHED)

    def test_成功绕行次数恒为零(self):
        """SEC-03 闸门绕行 = 0：计数器只增不减地记录尝试，成功恒为 0。"""
        h = make_harness()
        h.auto_advance()
        for _ in range(3):
            try:
                h.transition(Stage.S4, actor="编排器")
            except GateBypassError:
                pass
        counters = h.security_counters()
        self.assertEqual(counters["bypass_attempts"], 3)
        self.assertEqual(counters["bypass_blocked"], 3)
        self.assertEqual(counters["bypass_succeeded"], 0)


class TestGatedFlow(unittest.TestCase):
    """合法路径：闸门签核通过后才能继续流转。"""

    def test_签核通过后放行到S4(self):
        h = make_harness()
        h.auto_advance()
        h.sign_gate("GATE-A", signer=make_signer())
        self.assertIs(h.transition(Stage.S4, actor="编排器"), Stage.S4)
        self.assertEqual(h.security_counters()["gate_releases"], 1)

    def test_退回后重新提交并签核(self):
        h = make_harness()
        h.auto_advance()
        h.return_gate("GATE-A", signer=make_signer(), reason="证据不足")
        self.assertIs(h.stage, Stage.GATE_A)
        h.transition(Stage.S3, actor="编排器")
        h.transition(Stage.GATE_A, actor="编排器")
        self.assertEqual(h.case.gates["GATE-A"].submissions, 2)
        h.sign_gate("GATE-A", signer=make_signer())
        self.assertIs(h.transition(Stage.S4, actor="编排器"), Stage.S4)

    def test_闸门动作与状态迁移都写事件(self):
        h = make_harness()
        h.auto_advance()
        h.sign_gate("GATE-A", signer=make_signer())
        kinds = [e["kind"] for e in h.events]
        self.assertIn("stage_transition", kinds)
        self.assertIn("gate_signed", kinds)

    def test_非法状态迁移被拒并计数(self):
        h = make_harness()
        with self.assertRaises(TransitionError):
            h.transition(Stage.S5, actor="编排器")
        self.assertEqual(h.security_counters()["illegal_transitions"], 1)

    def test_人工改写进闸门记录(self):
        h = make_harness()
        h.auto_advance()
        h.record_amendment(
            "GATE-A",
            rule_id="R-CR-013",
            rule_version="3.2.0",
            original_rationale="原始意见",
            amended_rationale="修改后意见",
            reason="补充政策依据",
            amended_by="u-002",
        )
        gate = h.case.gates["GATE-A"]
        self.assertEqual(len(gate.amendments), 1)
        self.assertEqual(gate.amendments[0].original_rationale, "原始意见")


class TestHandoffFromCase(unittest.TestCase):
    def test_事实摘要缺失时交接被中断(self):
        h = make_harness()
        with self.assertRaises(HandoffIncompleteError):
            h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")

    def test_事实摘要齐备后交接通过(self):
        """评审修订：coverage 也是必填——此前编排器在 coverage=None 时
        伪造零值放行，违背 PRD 8.6「不得用默认值补齐」，本用例改为填齐后通过，
        零值伪造场景由 test_harness_gate_gaps 的专项断言钉住。"""
        h = make_harness()
        h.case.rule_binding = (
            RuleBinding(
                rule_id="R-CR-013",
                version="3.2.0",
                effective_from=date(2025, 7, 1),
                effective_to=None,
                binding_basis="三键唯一命中",
            ),
        )
        h.case.facts_digest = (
            {"field_path": "declared_revenue", "value": 820000,
             "confidence": "高", "conflict_state": "无"},
        )
        h.case.coverage = CoverageSummary(
            judged=1, not_applicable=0, unresolved=0, total=1)
        handoff = h.build_handoff(
            from_agent="审查 Agent", to_agent="风险研判 Agent"
        )
        self.assertEqual(handoff.case_id, h.case.case_id)
        self.assertEqual(handoff.severity_source, "rule_store")
        self.assertEqual(handoff.missing_fields(), ())