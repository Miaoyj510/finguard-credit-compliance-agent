"""切片 7 · 贷后闭环与 GATE-C 处置断言（PRD 6.4 FR-POST-01/05/06/07、FR-GATE-01/04）。"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.harness.gates import Signer
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import (
    GateBypassError,
    GateSignError,
    Stage,
)
from finguard.agents.post_loop import PostFlow
from finguard.agents.post_monitor import PostMonitorAgent
from finguard.agents.verifier import VerifierAgent
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile
from finguard.domain.post import (
    DISPOSITION_ACTIONS,
    PRE_DISPOSITION_LABELS,
    PostCaseView,
    PostDisposition,
    compute_post_metrics,
)
from finguard.rules.post_seeds import post_rule_map

NOW = "2026-03-01T09:00:00"
LATE = "2026-03-05T09:00:00"


def post_signer(**overrides):
    data = dict(
        account_id="u-post",
        display_name="周贷后",
        role="贷后管理岗",
        authority_level=3,
        authorized_case_types=("post_monitoring",),
        device_fingerprint="fp-post",
        ip="10.0.0.20",
    )
    data.update(overrides)
    return Signer(**data)


def build_flow(case_type="corp_credit_review"):
    case = CaseFile(
        case_id="FG-POST-000001",
        triple_key=TripleKey(date(2026, 1, 15), "小额经营贷", "二级分行"),
        case_type=case_type,
    )
    harness = Harness(case, clock=lambda: NOW)
    monitor = PostMonitorAgent(post_rule_map(), clock=lambda: NOW)
    verifier = VerifierAgent(clock=lambda: NOW)
    return case, harness, PostFlow(harness, monitor, verifier), monitor


def drive_to_s6(harness):
    """走完整主干路径到 S6（状态机不允许跳步，每一步都真实迁移）。"""
    harness.transition(Stage.S1, actor="编排器")
    harness.transition(Stage.S2, actor="编排器")
    harness.transition(Stage.S3, actor="编排器")
    harness.transition(Stage.GATE_A, actor="编排器")
    harness.sign_gate("GATE-A", signer=Signer(
        account_id="u-a", display_name="李合规", role="内控合规审查岗",
        authority_level=2, authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-a", ip="10.0.0.8"))
    harness.transition(Stage.S4, actor="编排器")
    harness.transition(Stage.GATE_B, actor="编排器")
    harness.sign_gate("GATE-B", signer=Signer(
        account_id="u-b", display_name="王研判", role="风险研判岗",
        authority_level=3, authorized_case_types=("corp_credit_review",),
        device_fingerprint="fp-b", ip="10.0.0.9"))
    harness.transition(Stage.S5, actor="编排器")
    harness.transition(Stage.S6, actor="编排器")


def post_view():
    return PostCaseView(
        case_id="FG-POST-000001",
        product_type="小额经营贷",
        org_level="二级分行",
        baseline_date="2026-01-15",
        rule_bindings=(("R-PM-001", "1.0.0"),),
        facts={"days_past_due": 45},
        field_meta={"days_past_due": {"channel": "api", "confidence": "高"}},
    )


def drive_to_gate(flow, harness, view=None):
    """把案件推到 GATE-C（已核查、已提交处置签核）。"""
    drive_to_s6(harness)
    flow.enter_monitoring(view or post_view())
    alerts = flow.scan("2026-03-01")
    flow.verify(alerts[0], {"days_past_due": 45})
    flow.submit_for_disposition(alerts[0])
    return alerts[0]


class TestPostFlow(unittest.TestCase):
    def test_必须先归档才能转入贷后(self):
        _case, harness, flow, _m = build_flow()
        with self.assertRaises(GateBypassError):
            flow.enter_monitoring(post_view())

    def test_转入后进入贷后循环并登记监测(self):
        _case, harness, flow, monitor = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        self.assertIs(harness.stage, Stage.POST_LOOP)
        self.assertEqual(monitor.monitored, ("FG-POST-000001",))

    def test_扫描命中进入预警态(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        self.assertTrue(alerts)
        self.assertIs(harness.stage, Stage.S7)

    def test_扫描未命中回到已归档(self):
        _case, harness, flow, _m = build_flow()
        view = PostCaseView(
            case_id="FG-POST-000001", product_type="小额经营贷", org_level="二级分行",
            baseline_date="2026-01-15", rule_bindings=(("R-PM-001", "1.0.0"),),
            facts={"days_past_due": 0},
        )
        drive_to_s6(harness)
        flow.enter_monitoring(view)
        self.assertEqual(flow.scan("2026-03-01"), ())
        self.assertIs(harness.stage, Stage.S6)

    def test_核查后进入核查态(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        self.assertIs(harness.stage, Stage.S8)

    def test_未核查可登记原因(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        reason = flow.mark_unverified(alerts[0], "NO_STAFF", "月末人力不足")
        self.assertEqual(reason.reason_code, "NO_STAFF")
        self.assertEqual(len(flow.unverified), 1)

    def test_提交处置进入闸门且待签核(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        self.assertIs(harness.stage, Stage.GATE_C)
        self.assertEqual(harness.case.gates["GATE-C"].state.value, "PENDING")

    def test_未签核不能处置(self):
        """处置必须发生在签核之后。"""
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        with self.assertRaises(GateBypassError):
            harness.transition(Stage.S9, actor="编排器")
        self.assertIs(harness.stage, Stage.GATE_C)

    def test_签核后处置并归档(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        disposed = flow.dispose(
            alerts[0], PostDisposition.FOLLOW_UP, post_signer(), reason="本月已电话核实"
        )
        self.assertIs(disposed.disposition, PostDisposition.FOLLOW_UP)
        self.assertIs(harness.stage, Stage.S9)
        flow.archive()
        self.assertIs(harness.stage, Stage.S10)

    def test_关闭档必须写理由(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        with self.assertRaises(ValueError):
            flow.dispose(alerts[0], PostDisposition.CLOSE, post_signer())

    def test_无资格账号签核被拒(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        with self.assertRaises(GateSignError):
            flow.dispose(
                alerts[0], PostDisposition.FOLLOW_UP,
                post_signer(role="客户经理"),
            )

    def test_超时只升级不放行(self):
        """FR-GATE-04：GATE-C 超时 48 小时只升级，闸门保持 PENDING。"""
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(post_view())
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        escalations = harness.escalate_overdue(LATE)
        self.assertEqual(len(escalations), 1)
        self.assertEqual(harness.case.gates["GATE-C"].state.value, "PENDING")
        with self.assertRaises(GateBypassError):
            harness.transition(Stage.S9, actor="编排器")


class TestDispositionEnums(unittest.TestCase):
    """FR-POST-05：四档封闭、每档绑定作业要求、与贷前两套枚举不同。"""

    def test_处置档位恰好四档(self):
        self.assertEqual(len(list(PostDisposition)), 4)

    def test_四档取值正确(self):
        self.assertEqual(
            sorted(d.value for d in PostDisposition),
            sorted(["关闭", "跟进", "上调关注", "触发现场检查"]),
        )

    def test_每档绑定作业要求(self):
        for d in PostDisposition:
            action = DISPOSITION_ACTIONS[d]
            self.assertTrue(action and action.strip(), "%s 缺作业要求" % d.value)

    def test_与贷前风险处置建议是两套不同枚举(self):
        """贷前四档（PRD 8.7 GATE-B）与贷后四档取值集合不相交。"""
        post_values = {d.value for d in PostDisposition}
        pre_values = set(PRE_DISPOSITION_LABELS)
        self.assertEqual(len(pre_values), 4)
        self.assertEqual(post_values & pre_values, set())

    def test_作业要求互不相同(self):
        actions = [DISPOSITION_ACTIONS[d] for d in PostDisposition]
        self.assertEqual(len(set(actions)), 4)


class TestPostMetrics(unittest.TestCase):
    """FR-POST-07：采纳率 / 误报率统一口径，可下钻到单条规则。"""

    def _alert(self, rule_id, disposition=None, alert_id=None):
        from finguard.domain.post import Alert, AlertLevel, freeze_snapshot
        return Alert(
            alert_id=alert_id or ("AL-%s" % rule_id),
            case_id="FG-1",
            rule_id=rule_id,
            rule_version="1.0.0",
            hit_at="2026-03-01",
            baseline_date="2026-01-15",
            lead_time_days=45,
            level=AlertLevel.HIGH,
            evidence=freeze_snapshot({"facts": {}}),
            disposition=disposition,
            disposed_by="u" if disposition else None,
            disposed_at="2026-03-02" if disposition else None,
        )

    def test_采纳率按处置档位算(self):
        alerts = [
            self._alert("R-PM-001", PostDisposition.FOLLOW_UP),
            self._alert("R-PM-002", PostDisposition.CLOSE),
            self._alert("R-PM-003", None),
        ]
        metrics = compute_post_metrics(alerts)
        self.assertEqual(metrics.total_alerts, 3)
        self.assertEqual(metrics.adopted, 1)
        self.assertAlmostEqual(metrics.adoption_rate, 1 / 3, places=5)

    def test_误报率以已核查为分母(self):
        from finguard.domain.post import Verification, VerificationOutcome
        alerts = [
            self._alert("R-PM-001", PostDisposition.CLOSE, "AL-1"),
            self._alert("R-PM-002", PostDisposition.FOLLOW_UP, "AL-2"),
        ]
        verifications = [
            Verification("AL-1", VerificationOutcome.UNCONFIRMED, "t", "核查 Agent", "值已变"),
            Verification("AL-2", VerificationOutcome.CONFIRMED, "t", "核查 Agent", "一致"),
        ]
        metrics = compute_post_metrics(alerts, verifications)
        self.assertEqual(metrics.verified, 2)
        self.assertEqual(metrics.false_positive, 1)
        self.assertAlmostEqual(metrics.false_positive_rate, 0.5, places=5)

    def test_可下钻到单条规则(self):
        alerts = [
            self._alert("R-PM-001", PostDisposition.FOLLOW_UP),
            self._alert("R-PM-001", PostDisposition.CLOSE),
            self._alert("R-PM-002", PostDisposition.FOLLOW_UP),
        ]
        drill = compute_post_metrics(alerts).drill_down()
        self.assertEqual(drill["R-PM-001"]["total"], 2)
        self.assertEqual(drill["R-PM-001"]["adopted"], 1)
        self.assertEqual(drill["R-PM-002"]["total"], 1)

    def test_漏报率不编造(self):
        """没有外部对照集就没有 ground truth，漏报率不实现也不填数。"""
        from finguard.domain.post import PostMetrics
        d = PostMetrics().to_dict()
        self.assertIn("miss_rate", d["caliber"])
        self.assertIn("不实现", d["caliber"]["miss_rate"])

    def test_口径写进输出(self):
        d = compute_post_metrics([]).to_dict()
        self.assertIn("adoption_rate", d["caliber"])
        self.assertIn("被采纳", d["caliber"]["adoption_rate"])