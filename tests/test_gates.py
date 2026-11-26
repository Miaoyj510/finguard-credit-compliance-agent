"""切片 5 · 三处人审闸门断言（PRD 6.5 FR-GATE-01 ~ 06、8.7 闸门表）。

本组断言几乎全部是**反向断言**：证明"不合规的签核全都进不来"。
"""
from __future__ import annotations

import unittest

from finguard.agents.harness.gates import GateState, Signer, build_gates
from finguard.agents.harness.states import GateSignError, Stage

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


class TestGateLifecycle(unittest.TestCase):
    def setUp(self):
        self.gates = build_gates()
        self.gate = self.gates["GATE-A"]

    # ---------------- 正常路径（唯一能让闸门放行的路径） ----------------
    def test_提交后进入待签核并起算超时(self):
        self.gate.open(NOW)
        self.assertIs(self.gate.state, GateState.PENDING)
        self.assertEqual(self.gate.deadline, "2026-11-27T10:00:00")
        self.assertEqual(self.gate.submissions, 1)

    def test_合格签核放行且证据齐全(self):
        self.gate.open(NOW)
        self.gate.sign(signer(), NOW)
        self.assertIs(self.gate.state, GateState.APPROVED)
        evidence = self.gate.approval_evidence()
        self.assertEqual(evidence["signed_by"], "u-001")
        self.assertEqual(evidence["device_fingerprint"], "fp-abc")
        self.assertEqual(evidence["ip"], "10.0.0.8")
        self.assertTrue(evidence["signed_at"])

    def test_三处闸门规格齐备(self):
        self.assertEqual(set(self.gates), {"GATE-A", "GATE-B", "GATE-C"})
        self.assertEqual(self.gates["GATE-B"].timeout_hours, 24)
        self.assertEqual(self.gates["GATE-C"].timeout_hours, 48)
        self.assertIs(self.gates["GATE-C"].stage, Stage.GATE_C)

    # ---------------- 资格校验（FR-GATE-02） ----------------
    def test_岗位不符被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(role="客户经理"), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_授权层级不足被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(authority_level=1), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_案件类型不符被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(authorized_case_types=("post_monitoring",)), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    # ---------------- 不可代签（FR-GATE-03） ----------------
    def test_批量导入式签核被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(batch_mode=True), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_缺设备指纹被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(device_fingerprint=""), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_缺IP被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(ip=""), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_缺实名账号被拒(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(account_id=""), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    # ---------------- 职责分离（G-5） ----------------
    def test_规则发布人不得签核(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(is_rule_author=True), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_意见生成Agent运行主体不得签核(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(is_agent_principal=True), NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    # ---------------- 幂等与状态 ----------------
    def test_重复签核被拒且不覆盖原签核(self):
        self.gate.open(NOW)
        self.gate.sign(signer(), NOW)
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(account_id="u-999"), NOW)
        self.assertEqual(self.gate.signed_by, "u-001")

    def test_未提交到闸门不得签核(self):
        with self.assertRaises(GateSignError):
            self.gate.sign(signer(), NOW)
        self.assertIs(self.gate.state, GateState.NOT_REACHED)

    def test_退回必须填理由(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.return_to_author(signer(), "  ", NOW)
        self.assertIs(self.gate.state, GateState.PENDING)

    def test_退回后状态为已退回且记录理由(self):
        self.gate.open(NOW)
        self.gate.return_to_author(signer(), "证据不足以支撑命中结论", NOW)
        self.assertIs(self.gate.state, GateState.RETURNED)
        self.assertEqual(self.gate.returned_reason, "证据不足以支撑命中结论")

    # ---------------- 超时只升级、不放行（FR-GATE-04） ----------------
    def test_超时只升级不放行(self):
        self.gate.open(NOW)
        escalation = self.gate.check_timeout(LATE)
        self.assertIsNotNone(escalation)
        self.assertEqual(escalation.to_role, "上级合规负责人")
        self.assertIs(self.gate.state, GateState.PENDING)
        self.assertIsNone(self.gate.signed_by)
        self.assertIsNone(self.gate.approval_evidence())

    def test_未超时不升级(self):
        self.gate.open(NOW)
        self.assertIsNone(self.gate.check_timeout("2026-11-26T20:00:00"))
        self.assertEqual(self.gate.escalations, ())

    def test_升级链按序逐级触发(self):
        self.gate.open(NOW)
        first = self.gate.check_timeout(LATE)
        second = self.gate.check_timeout(LATE)
        self.assertEqual(first.level, 1)
        self.assertEqual(second.level, 2)
        self.assertEqual(second.to_role, "风控负责人")
        self.assertIsNone(self.gate.check_timeout(LATE))   # 已到最高级
        self.assertIs(self.gate.state, GateState.PENDING)

    # ---------------- 人工改写双版本（FR-GATE-06） ----------------
    def test_人工改写保留原始与修改后双版本(self):
        self.gate.open(NOW)
        amendment = self.gate.record_amendment(
            rule_id="R-CR-013",
            rule_version="3.2.0",
            original_rationale="原始意见：流水与申报营收偏离",
            amended_rationale="修改后：偏离在授信政策容忍区间内",
            reason="补充政策依据",
            amended_by="u-002",
            amended_at=NOW,
        )
        self.assertEqual(amendment.original_rationale, "原始意见：流水与申报营收偏离")
        self.assertEqual(len(self.gate.amendments), 1)

    def test_人工改写必须填理由(self):
        self.gate.open(NOW)
        with self.assertRaises(GateSignError):
            self.gate.record_amendment(
                rule_id="R-CR-013", rule_version="3.2.0",
                original_rationale="a", amended_rationale="b", reason="",
                amended_by="u-002", amended_at=NOW,
            )

    def test_原始意见只增不改(self):
        """改写只追加，不提供覆盖原始意见的入口。"""
        self.gate.open(NOW)
        self.gate.record_amendment(
            rule_id="R-CR-013", rule_version="3.2.0",
            original_rationale="原始", amended_rationale="改后", reason="理由",
            amended_by="u-002", amended_at=NOW,
        )
        self.gate.record_amendment(
            rule_id="R-CR-014", rule_version="1.0.0",
            original_rationale="原始2", amended_rationale="改后2", reason="理由2",
            amended_by="u-002", amended_at=NOW,
        )
        self.assertEqual(len(self.gate.amendments), 2)
        self.assertEqual(self.gate.amendments[0].original_rationale, "原始")

    def test_闸门记录可序列化且含签核与升级(self):
        self.gate.open(NOW)
        self.gate.check_timeout(LATE)
        self.gate.sign(signer(), LATE)
        d = self.gate.to_dict()
        self.assertEqual(d["gate_id"], "GATE-A")
        self.assertEqual(d["state"], "APPROVED")
        self.assertEqual(len(d["escalations"]), 1)