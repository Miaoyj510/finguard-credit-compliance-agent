"""切片 5 · 评审咨询 C1 的决策断言（PRD 8.7 升级链属超时路径）。

C1 决策：**升级链上的上级岗位必须先等到超时升级发生，才可接手签核。**
理由：PRD 8.7 把升级链写在**超时路径**里；未超时就让上级接签，
等同于 L1 静默放弃签核权——那是"绕过"，不是"升级"。
"""
from __future__ import annotations

import unittest

from finguard.agents.harness.gates import GateState, Signer, build_gates
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import GateSignError, Stage
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile

from datetime import date

NOW = "2026-11-26T10:00:00"
LATE = "2026-11-28T10:00:00"


def make_harness(now=NOW):
    case = CaseFile(
        case_id="FG-C1-000001",
        triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
    )
    return Harness(case, clock=lambda: now)


def base_signer(**overrides):
    data = dict(
        account_id="u-l1",
        display_name="李合规",
        role="内控合规审查岗",
        authority_level=2,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-l1",
        ip="10.0.0.8",
    )
    data.update(overrides)
    return Signer(**data)


def boss_signer(**overrides):
    data = dict(
        account_id="u-boss",
        display_name="王上级",
        role="上级合规负责人",
        authority_level=4,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-boss",
        ip="10.0.0.9",
    )
    data.update(overrides)
    return Signer(**data)


class TestEscalationChainCannotJumpIn(unittest.TestCase):
    def test_未超时上级岗位不可提前接签(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=boss_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)

    def test_超时升级后上级岗位可接签(self):
        h = make_harness()
        h.auto_advance()
        h.escalate_overdue(LATE)
        h.sign_gate("GATE-A", signer=boss_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.APPROVED)

    def test_基础岗位不受影响(self):
        h = make_harness()
        h.auto_advance()
        h.sign_gate("GATE-A", signer=base_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.APPROVED)

    def test_抢签被拒有留痕且计数(self):
        """SEC-04：被拒次数须完整记录——抢签也要留痕。"""
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=boss_signer())
        self.assertEqual(h.security_counters()["sign_rejections"], 1)
        kinds = [e["kind"] for e in h.events]
        self.assertIn("gate_sign_rejected", kinds)

    def test_拒签后基础岗位仍可正常签核(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=boss_signer())
        h.sign_gate("GATE-A", signer=base_signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.APPROVED)

    def test_退回同样受升级链约束(self):
        """退回也是闸门签核动作：结论取反也是结论。"""
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.return_gate("GATE-A", signer=boss_signer(), reason="证据不足")
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)