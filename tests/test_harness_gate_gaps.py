"""切片 5 评审补充断言：auto_advance 停闸语义、Handoff 契约保真、
退回资格、拒签留痕与时间戳健壮性。

每条都对应评审探针 R1—R10 确认的一个缺陷，先红后绿。
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from finguard.agents.harness.gates import GateState, Signer, build_gates
from finguard.agents.harness.handoff import Handoff
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import (
    GateSignError,
    HandoffIncompleteError,
    Stage,
)
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile
from finguard.domain.enums import UnresolvedReasonCode
from finguard.domain.opinion import CoverageSummary
from finguard.rules.registry import RuleBinding, UnresolvedRule

NOW = "2026-11-26T10:00:00"
LATE = "2026-11-28T10:00:00"


def signer(**overrides):
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


def _coverage(judged=1, na=0, unresolved=1, total=2):
    return CoverageSummary(judged=judged, not_applicable=na,
                           unresolved=unresolved, total=total)


def make_harness(now=NOW, case_type="corp_credit_review"):
    case = CaseFile(
        case_id="FG-REVIEW-01",
        triple_key=TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
        case_type=case_type,
    )
    return Harness(case, clock=lambda: now)


def handoff_ready(h, unresolved=()):
    """把案件填到「可以交接」的最小完备状态（事实/绑定/覆盖度齐备）。"""
    h.case.rule_binding = (
        RuleBinding(rule_id="R-CR-013", version="3.2.0",
                    effective_from=date(2025, 7, 1), effective_to=None,
                    binding_basis="三键唯一命中"),
    )
    h.case.facts_digest = (
        {"field_path": "declared_revenue", "value": 820000,
         "confidence": "高", "conflict_state": "无"},
    )
    h.case.unresolved = tuple(unresolved)
    h.case.coverage = _coverage(
        unresolved=len(unresolved) + 1, total=len(unresolved) + 2)
    return h


class TestAutoAdvanceAtGate(unittest.TestCase):
    """探针 R1/R2：auto_advance 的「遇闸必停」必须是真停下，
    既不能静默空转，也不能把例行推进计成绕行尝试。"""

    def test_停在闸门时再推进直接返回不产生绕行计数(self):
        h = make_harness()
        h.auto_advance()
        self.assertIs(h.stage, Stage.GATE_A)
        before = h.security_counters()
        # 编排器例行轮询：停在闸门上必须原样返回，不得抛 GateBypassError、
        # 不得给 SEC-03 的 bypass_attempts 添噪声
        self.assertIs(h.auto_advance(), Stage.GATE_A)
        self.assertIs(h.auto_advance(), Stage.GATE_A)
        after = h.security_counters()
        self.assertEqual(after["bypass_attempts"], before["bypass_attempts"])
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)

    def test_退回后自动推进重新进闸门(self):
        h = make_harness()
        h.auto_advance()
        h.return_gate("GATE-A", signer=signer(), reason="证据不足")
        h.transition(Stage.S3, actor="编排器")
        self.assertIs(h.stage, Stage.S3)
        # 修订后自动推进：必须真的重新进闸门（PENDING），而不是停在 S3 谎报已到
        self.assertIs(h.auto_advance(), Stage.GATE_A)
        self.assertIs(h.stage, Stage.GATE_A)
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)
        self.assertEqual(h.case.gates["GATE-A"].submissions, 2)
        # 重新进闸后仍可正常签核放行
        h.sign_gate("GATE-A", signer=signer())
        self.assertIs(h.transition(Stage.S4, actor="编排器"), Stage.S4)


class TestHandoffFaithfulReporting(unittest.TestCase):
    """探针 R3/R4/R5/R7：Handoff 是 Agent 间唯一上下文契约，
    报错误导比不报更危险（PRD 8.6）。"""

    def test_停在GATE_B时报告当前闸门而非最早的GATE_A(self):
        h = handoff_ready(make_harness())
        h.auto_advance()
        h.sign_gate("GATE-A", signer=signer())
        h.transition(Stage.S4, actor="编排器")
        h.transition(Stage.GATE_B, actor="编排器")
        self.assertIs(h.stage, Stage.GATE_B)
        ho = h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")
        state = dict(ho.human_gate_state)
        self.assertEqual(state["gate_id"], "GATE-B")
        self.assertEqual(state["state"], "PENDING")
        self.assertFalse(state["has_signature"])

    def test_handoff携带未判定规则及原因码(self):
        h = handoff_ready(
            make_harness(),
            unresolved=(UnresolvedRule(
                rule_id="R-CR-021",
                reason_code=UnresolvedReasonCode.AMBIGUOUS_VERSION,
                detail="两版重叠", candidates=("1.4.1", "1.5.0")),),
        )
        ho = h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")
        entries = [dict(i) for i in ho.rule_binding]
        unresolved_entries = [e for e in entries if e.get("unresolved_reason_code")]
        self.assertEqual(len(unresolved_entries), 1)
        self.assertEqual(unresolved_entries[0]["rule_id"], "R-CR-021")
        self.assertEqual(unresolved_entries[0]["unresolved_reason_code"],
                         "AMBIGUOUS_VERSION")
        # 已绑定条目不受影响
        bound = [e for e in entries if not e.get("unresolved_reason_code")]
        self.assertEqual(bound[0]["version"], "3.2.0")

    def test_未判定条目允许缺版本号但必须有原因码(self):
        base = dict(
            handoff_id="HO-X-1", case_id="FG-X", from_agent="审查 Agent",
            to_agent="风险研判 Agent", stage=Stage.S3,
            facts_digest=({"field_path": "a", "value": 1, "confidence": "高",
                           "conflict_state": "无"},),
            citation_bundle=(), open_questions=(),
            coverage_summary={"judged": 0, "not_applicable": 0,
                             "unresolved": 1, "total": 1},
            human_gate_state={"gate_id": "GATE-A", "state": "PENDING",
                              "has_signature": False},
            produced_at=NOW, producer_spec_version="harness-1.0",
            severity_source="rule_store",
        )
        # 未判定项没有唯一版本是常态（NO_VERSION_MATCH 根本没有版本可写）
        Handoff(rule_binding=({"rule_id": "R-CR-021", "version": None,
                               "binding_basis": "须人工判定",
                               "unresolved_reason_code": "NO_VERSION_MATCH"},),
                **base).validate()
        # 已绑定条目缺版本仍然要拒
        with self.assertRaises(HandoffIncompleteError):
            Handoff(rule_binding=({"rule_id": "R-CR-013",
                                   "binding_basis": "三键唯一命中"},),
                    **base).validate()

    def test_闸门状态缺是否有签核记录被拒(self):
        # PRD 8.6：human_gate_state 必含 gate_id / state / 是否有签核记录
        with self.assertRaises(HandoffIncompleteError):
            Handoff(
                handoff_id="HO-X-1", case_id="FG-X", from_agent="审查 Agent",
                to_agent="风险研判 Agent", stage=Stage.S3,
                rule_binding=({"rule_id": "R-CR-013", "version": "3.2.0",
                               "binding_basis": "三键唯一命中"},),
                facts_digest=({"field_path": "a", "value": 1, "confidence": "高",
                               "conflict_state": "无"},),
                citation_bundle=(), open_questions=(),
                coverage_summary={"judged": 1, "not_applicable": 0,
                                 "unresolved": 0, "total": 1},
                human_gate_state={"gate_id": "GATE-A", "state": "PENDING"},
                produced_at=NOW, producer_spec_version="harness-1.0",
                severity_source="rule_store",
            ).validate()

    def test_覆盖度缺失不得用零值伪造补齐(self):
        h = make_harness()
        h.case.rule_binding = (
            RuleBinding(rule_id="R-CR-013", version="3.2.0",
                        effective_from=date(2025, 7, 1), effective_to=None,
                        binding_basis="三键唯一命中"),)
        h.case.facts_digest = (
            {"field_path": "declared_revenue", "value": 820000,
             "confidence": "高", "conflict_state": "无"},)
        # case.coverage is None —— 编排器不得凭空造出「已判定 0 条」
        with self.assertRaises(HandoffIncompleteError):
            h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")

    def test_handoff编号连续调用也唯一(self):
        h = handoff_ready(make_harness())
        a = h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")
        b = h.build_handoff(from_agent="审查 Agent", to_agent="风险研判 Agent")
        self.assertNotEqual(a.handoff_id, b.handoff_id)


class TestReturnQualification(unittest.TestCase):
    """探针 R6：退回同样是闸门动作——必须过资格校验并留完整痕迹。"""

    def test_无资格账号退回被拒(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.return_gate("GATE-A", signer=signer(role="客户经理"),
                          reason="恶意占位")
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)
        self.assertIsNone(h.case.gates["GATE-A"].returned_by)

    def test_批量导入式退回被拒(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.return_gate("GATE-A", signer=signer(batch_mode=True),
                          reason="批量退回")
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)

    def test_退回记录设备指纹与IP(self):
        h = make_harness()
        h.auto_advance()
        h.return_gate("GATE-A", signer=signer(), reason="证据不足")
        gate = h.case.gates["GATE-A"]
        self.assertEqual(gate.returned_by, "u-001")
        self.assertEqual(gate.returned_device_fingerprint, "fp-abc")
        self.assertEqual(gate.returned_ip, "10.0.0.8")
        d = gate.to_dict()
        self.assertEqual(d["returned_device_fingerprint"], "fp-abc")


class TestSignRejectionTrail(unittest.TestCase):
    """SEC-04 口径：「被拒次数须完整记录」；FR-GATE-02：「拦截日志可查」。"""

    def test_拒签必须留下可查事件与计数(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=signer(role="客户经理"))
        kinds = [e["kind"] for e in h.events]
        self.assertIn("gate_sign_rejected", kinds)
        rejected = [e for e in h.events if e["kind"] == "gate_sign_rejected"][0]
        self.assertEqual(rejected["detail"]["gate_id"], "GATE-A")
        self.assertEqual(rejected["detail"]["role"], "客户经理")
        self.assertTrue(rejected["detail"]["reason"])
        self.assertEqual(h.security_counters()["sign_rejections"], 1)
        # 拒签之后合格签核仍可正常进行
        h.sign_gate("GATE-A", signer=signer())
        self.assertIs(h.case.gates["GATE-A"].state, GateState.APPROVED)

    def test_批量代签被拒同样留痕(self):
        h = make_harness()
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=signer(batch_mode=True))
        kinds = [e["kind"] for e in h.events]
        self.assertIn("gate_sign_rejected", kinds)


class TestCaseTypeAuthorization(unittest.TestCase):
    """FR-GATE-02「案件类型」校验的对象是**本案**，不是闸门规格的静态字符串。"""

    def test_对本案案件类型有授权的账号可签核(self):
        h = make_harness(case_type="personal_loan")
        h.auto_advance()
        # 该账号有权签「个人贷款」类案件——本案正是个人贷款
        h.sign_gate("GATE-A", signer=signer(authorized_case_types=("personal_loan",)))
        self.assertIs(h.case.gates["GATE-A"].state, GateState.APPROVED)

    def test_对本案类型无授权仍被拒(self):
        h = make_harness(case_type="personal_loan")
        h.auto_advance()
        with self.assertRaises(GateSignError):
            h.sign_gate("GATE-A", signer=signer(authorized_case_types=("corp_credit_review",)))
        self.assertIs(h.case.gates["GATE-A"].state, GateState.PENDING)


class TestTimestampRobustness(unittest.TestCase):
    """探针 R9/R10：时间戳是全链路口供，坏值必须响亮拒绝而不是崩溃或静默。"""

    def test_微秒精度时钟不再崩溃(self):
        h = make_harness(now="2026-11-26T10:00:00.123456")
        self.assertIs(h.auto_advance(), Stage.GATE_A)
        gate = h.case.gates["GATE-A"]
        self.assertEqual(gate.state, GateState.PENDING)
        # deadline 可被解析且晚于提交时刻
        datetime.fromisoformat(gate.deadline)

    def test_异形但可解析的deadline提交时被规范化入库(self):
        gate = build_gates()["GATE-A"]
        gate.open(NOW, deadline="2026-12-01 08:00")
        # 入库必须规范化为同一可比口径（ISO T 分隔），否则超时判定静默失效（R10）
        self.assertEqual(gate.deadline, "2026-12-01T08:00:00")

    def test_无法解析的deadline在提交时即被拒(self):
        gate = build_gates()["GATE-A"]
        with self.assertRaises(GateSignError):
            gate.open(NOW, deadline="2026年12年01日 08:00")

    def test_签核时间不可解析被拒且不留半成品证据(self):
        gate = build_gates()["GATE-A"]
        gate.open(NOW)
        with self.assertRaises(GateSignError):
            gate.sign(signer(), "昨天下午")
        self.assertIs(gate.state, GateState.PENDING)
        self.assertIsNone(gate.approval_evidence())

    def test_带时区的时间戳可正常比较(self):
        gate = build_gates()["GATE-A"]
        # deadline = 26 日 10:00(+08) → UTC 02:00 → +24h → 27 日 02:00 UTC
        gate.open("2026-11-26T10:00:00+08:00")
        self.assertEqual(gate.deadline, "2026-11-27T02:00:00")
        # 27 日 09:58(+08) = 01:58 UTC，未超时
        self.assertIsNone(gate.check_timeout("2026-11-27T09:58:00+08:00"))
        # 27 日 10:00:01(+08) = 02:00:01 UTC，已超时
        self.assertIsNotNone(gate.check_timeout("2026-11-27T10:00:01+08:00"))


if __name__ == "__main__":
    unittest.main()
