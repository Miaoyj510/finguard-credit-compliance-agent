"""切片 7 评审补充断言：常驻循环的连续性、跨案保护。

R-P1：幂等去重不得把「新事实的新命中」也吞掉——常驻循环的价值就在
     持续监测；R-P2：核查/处置不得接受他案预警。先红后绿。
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.harness.gates import Signer
from finguard.agents.harness.orchestrator import Harness
from finguard.agents.harness.states import Stage
from finguard.agents.post_loop import PostFlow
from finguard.agents.post_monitor import PostMonitorAgent
from finguard.agents.verifier import VerifierAgent
from finguard.domain.case import TripleKey
from finguard.domain.case_state import CaseFile
from finguard.domain.post import Alert, AlertLevel, AlertStatus, PostCaseView, PostDisposition
from finguard.rules.post_seeds import post_rule_map

NOW = "2026-03-01T09:00:00"


def post_signer():
    return Signer(
        account_id="u-post", display_name="周贷后", role="贷后管理岗",
        authority_level=3, authorized_case_types=("post_monitoring",),
        device_fingerprint="fp-post", ip="10.0.0.20",
    )


def credit_signer(role, account, fp, ip):
    return Signer(
        account_id=account, display_name=account, role=role,
        authority_level=3, authorized_case_types=("corp_credit_review",),
        device_fingerprint=fp, ip=ip,
    )


def view_with(dpd):
    return PostCaseView(
        case_id="FG-POST-000001",
        product_type="小额经营贷",
        org_level="二级分行",
        baseline_date="2026-01-15",
        rule_bindings=(("R-PM-001", "1.0.0"),),
        facts={"days_past_due": dpd},
        field_meta={"days_past_due": {"channel": "api", "confidence": "高"}},
    )


def build_flow():
    case = CaseFile(
        case_id="FG-POST-000001",
        triple_key=TripleKey(date(2026, 1, 15), "小额经营贷", "二级分行"),
    )
    harness = Harness(case, clock=lambda: NOW)
    monitor = PostMonitorAgent(post_rule_map(), clock=lambda: NOW)
    verifier = VerifierAgent(clock=lambda: NOW)
    return case, harness, PostFlow(harness, monitor, verifier), monitor


def drive_to_s6(harness):
    harness.transition(Stage.S1, actor="编排器")
    harness.transition(Stage.S2, actor="编排器")
    harness.transition(Stage.S3, actor="编排器")
    harness.transition(Stage.GATE_A, actor="编排器")
    harness.sign_gate("GATE-A", signer=credit_signer("内控合规审查岗", "u-a", "fp-a", "10.0.0.8"))
    harness.transition(Stage.S4, actor="编排器")
    harness.transition(Stage.GATE_B, actor="编排器")
    harness.sign_gate("GATE-B", signer=credit_signer("风险研判岗", "u-b", "fp-b", "10.0.0.9"))
    harness.transition(Stage.S5, actor="编排器")
    harness.transition(Stage.S6, actor="编排器")


class TestMonitoringContinuity(unittest.TestCase):
    """R-P1：幂等防的是「同事实重复报」，不是「新事实不再报」。"""

    def test_事实变化后同一规则产生新预警(self):
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: NOW)
        monitor.start()
        monitor.register(view_with(45))
        first = monitor.scan_once("2026-03-01")
        self.assertEqual(len(first), 1)
        # 下一期扫描：逾期天数变了（新的事实），常驻循环必须再次预警
        monitor.register(view_with(75))
        second = monitor.scan_once("2026-04-01")
        self.assertEqual(len(second), 1, "新事实被幂等键吞掉——常驻监测实际只报一次")
        self.assertNotEqual(first[0].alert_id, second[0].alert_id)
        self.assertEqual(second[0].lead_time_days, 76)   # 2026-04-01 − 放款日 2026-01-15

    def test_同事实重复扫描仍幂等(self):
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: NOW)
        monitor.start()
        monitor.register(view_with(45))
        self.assertEqual(len(monitor.scan_once("2026-03-01")), 1)
        self.assertEqual(monitor.scan_once("2026-03-02"), ())

    def test_处置后同事实不重复报(self):
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: NOW)
        monitor.start()
        monitor.register(view_with(45))
        monitor.scan_once("2026-03-01")
        monitor.scan_once("2026-03-01")
        self.assertEqual(len(monitor.alerts), 1)


class TestCrossCaseGuard(unittest.TestCase):
    """R-P2：核查与处置只接受本案预警。"""

    def _foreign_alert(self):
        return Alert(
            alert_id="AL-OTHER-R-PM-001", case_id="FG-OTHER-000002",
            rule_id="R-PM-001", rule_version="1.0.0",
            hit_at="2026-03-01", baseline_date="2026-01-15",
            lead_time_days=45, level=AlertLevel.HIGH,
            evidence={"facts": {"days_past_due": {"value": 45}}},
            status=AlertStatus.NEW,
        )

    def test_核查拒绝他案预警(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(view_with(45))
        flow.scan("2026-03-01")
        with self.assertRaises(ValueError):
            flow.verify(self._foreign_alert(), {"days_past_due": 45})

    def test_处置拒绝他案预警(self):
        _case, harness, flow, _m = build_flow()
        drive_to_s6(harness)
        flow.enter_monitoring(view_with(45))
        alerts = flow.scan("2026-03-01")
        flow.verify(alerts[0], {"days_past_due": 45})
        flow.submit_for_disposition(alerts[0])
        with self.assertRaises(ValueError):
            flow.dispose(self._foreign_alert(), PostDisposition.CLOSE,
                         post_signer(), reason="引用核查结论")
        # 他案预警不得留下签核与处置痕迹
        self.assertEqual(flow.disposed, ())


if __name__ == "__main__":
    unittest.main()


class TestScanSkipDoesNotBreakRound(unittest.TestCase):
    """C1：单案构造失败**按案件跳过并留痕**，不中断整轮扫描。"""

    def _view(self, case_id, baseline, days_past_due):
        from finguard.domain.post import PostCaseView
        return PostCaseView(
            case_id=case_id, product_type="小额经营贷", org_level="二级分行",
            baseline_date=baseline,
            rule_bindings=(("R-PM-001", "1.0.0"),),
            facts={"days_past_due": days_past_due},
            field_meta={"days_past_due": {"channel": "api", "confidence": "高"}},
        )

    def test_单案跳过不中断整轮(self):
        from finguard.agents.post_monitor import PostMonitorAgent
        from finguard.rules.post_seeds import post_rule_map
        events = []
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00",
                                   event_sink=events.append)
        monitor.register(self._view("FG-BAD", "2026-06-01", 45))     # as_of 早于基准日
        monitor.register(self._view("FG-OK", "2026-01-01", 45))
        monitor.start()
        alerts = monitor.scan_once("2026-03-01")
        self.assertEqual([a.case_id for a in alerts], ["FG-OK"])

    def test_跳过有事件留痕(self):
        from finguard.agents.post_monitor import PostMonitorAgent
        from finguard.rules.post_seeds import post_rule_map
        events = []
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00",
                                   event_sink=events.append)
        monitor.register(self._view("FG-BAD", "2026-06-01", 45))
        monitor.start()
        monitor.scan_once("2026-03-01")
        kinds = [e["kind"] for e in monitor.events]
        self.assertIn("scan_skipped", kinds)
        self.assertTrue(events, "跳过必须经事件出口留痕，不能只留在内存")

    def test_跳过事件带案件与原因(self):
        from finguard.agents.post_monitor import PostMonitorAgent
        from finguard.rules.post_seeds import post_rule_map
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")
        monitor.register(self._view("FG-BAD", "2026-06-01", 45))
        monitor.start()
        monitor.scan_once("2026-03-01")
        detail = monitor.events[0]["detail"]
        self.assertEqual(detail["case_id"], "FG-BAD")
        self.assertTrue(detail["reason"])


class TestIdempotencyCaliber(unittest.TestCase):
    """C2：事实指纹不变则不重报（含已处置后）；事实变了才重报。"""

    def _monitor(self):
        from finguard.agents.post_monitor import PostMonitorAgent
        from finguard.rules.post_seeds import post_rule_map
        return PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")

    def _view(self, days_past_due):
        from finguard.domain.post import PostCaseView
        return PostCaseView(
            case_id="FG-1", product_type="小额经营贷", org_level="二级分行",
            baseline_date="2026-01-01",
            rule_bindings=(("R-PM-001", "1.0.0"),),
            facts={"days_past_due": days_past_due},
            field_meta={"days_past_due": {"channel": "api", "confidence": "高"}},
        )

    def test_处置后事实未变不重报(self):
        monitor = self._monitor()
        monitor.register(self._view(45))
        monitor.start()
        first = monitor.scan_once("2026-03-01")
        self.assertEqual(len(first), 1)
        # 模拟已处置：事实未变，下一期不再报
        again = monitor.scan_once("2026-04-01")
        self.assertEqual(again, ())

    def test_事实变了重报(self):
        monitor = self._monitor()
        monitor.register(self._view(45))
        monitor.start()
        monitor.scan_once("2026-03-01")
        monitor.register(self._view(75))          # 逾期天数变化 = 新事实
        later = monitor.scan_once("2026-04-01")
        self.assertEqual(len(later), 1)
        self.assertNotEqual(later[0].alert_id, monitor.alerts[0].alert_id)
