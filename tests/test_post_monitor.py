"""切片 7 · 贷后监测 Agent 断言（PRD 6.4 FR-POST-01 ~ 04）。"""
from __future__ import annotations

import unittest

from finguard.agents.post_monitor import PostMonitorAgent
from finguard.domain.post import AlertLevel, PostCaseView
from finguard.rules.post_seeds import (
    REUSED_CREDIT_RULE_ID,
    REUSED_CREDIT_RULE_VERSION,
    post_rule_map,
)
from finguard.rules.seeds import RULES as CREDIT_RULES


DEFAULT_FACTS = {
    "days_past_due": 45,
    "rating_downgrade": 0,
    "utilization_ratio": 0.4,
    "monthly_revenue": 820000,
    "declared_revenue": 1000000,
}
DEFAULT_META = {
    "days_past_due": {"channel": "api", "confidence": "高"},
    "rating_downgrade": {"channel": "api", "confidence": "高"},
    "utilization_ratio": {"channel": "api", "confidence": "高"},
    "monthly_revenue": {"channel": "ocr", "confidence": "高"},
    "declared_revenue": {"channel": "ocr", "confidence": "高"},
}


def view(case_id="FG-POST-000001", baseline="2026-01-15", facts=None, meta=None):
    data = dict(DEFAULT_FACTS) if facts is None else dict(facts)
    metas = dict(DEFAULT_META) if meta is None else dict(meta)
    return PostCaseView(
        case_id=case_id,
        product_type="小额经营贷",
        org_level="二级分行",
        baseline_date=baseline,
        rule_bindings=(
            ("R-PM-001", "1.0.0"),
            ("R-PM-002", "1.1.0"),
            ("R-PM-003", "1.0.0"),
            (REUSED_CREDIT_RULE_ID, REUSED_CREDIT_RULE_VERSION),
        ),
        facts=data,
        field_meta=metas,
    )


class TestPostMonitorLoop(unittest.TestCase):
    """FR-POST-01：常驻循环与主流程解耦，可独立启停。"""

    def setUp(self):
        self.monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")

    def test_未启动不扫描(self):
        self.monitor.register(view())
        self.assertEqual(self.monitor.scan_once("2026-03-01"), ())

    def test_可独立启停(self):
        self.monitor.register(view())
        self.monitor.start()
        self.assertTrue(self.monitor.running)
        self.assertTrue(self.monitor.scan_once("2026-03-01"))
        self.monitor.stop()
        self.assertFalse(self.monitor.running)
        self.assertEqual(self.monitor.scan_once("2026-03-01"), ())

    def test_只读视图不含主流程案件引用(self):
        """解耦是结构性的：视图里没有 CaseFile。"""
        self.assertFalse(hasattr(view(), "case"))
        self.assertFalse(hasattr(view(), "gates"))

    def test_监测名单可增删(self):
        self.monitor.register(view("FG-A"))
        self.monitor.register(view("FG-B"))
        self.assertEqual(self.monitor.monitored, ("FG-A", "FG-B"))
        self.monitor.unregister("FG-A")
        self.assertEqual(self.monitor.monitored, ("FG-B",))


class TestAlertPayload(unittest.TestCase):
    """FR-POST-02 / 03 / 04：预警该带什么。"""

    def setUp(self):
        self.monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")
        self.monitor.register(view())
        self.monitor.start()
        self.alerts = self.monitor.scan_once("2026-03-01")
        self.alert = [a for a in self.alerts if a.rule_id == "R-PM-001"][0]

    def test_命中生成预警(self):
        # 两条命中：逾期（R-PM-001）+ 复用的贷前规则（流水 82 万 < 申报 100 万）
        self.assertEqual(len(self.alerts), 2)

    def test_未命中不生成预警(self):
        hit = {a.rule_id for a in self.alerts}
        self.assertNotIn("R-PM-002", hit)      # 评级没下调
        self.assertNotIn("R-PM-003", hit)      # 使用率不高

    def test_预警带规则编号与版本(self):
        self.assertEqual(self.alert.rule_id, "R-PM-001")
        self.assertEqual(self.alert.rule_version, "1.0.0")

    def test_规则版本与贷前一致(self):
        """FR-POST-02：同一 rule_id 在贷前贷后两处的 version 语义一致。

        贷后复用了贷前的 R-CR-013，它在贷后场景里照样命中，
        且取的版本与贷前**同一个版本号**。
        """
        credit = [r for r in CREDIT_RULES
                  if r.rule_id == REUSED_CREDIT_RULE_ID
                  and r.version == REUSED_CREDIT_RULE_VERSION][0]
        reused = [a for a in self.alerts if a.rule_id == REUSED_CREDIT_RULE_ID]
        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0].rule_version, credit.version)

    def test_预警带命中时点与基准日(self):
        self.assertEqual(self.alert.hit_at, "2026-03-01")
        self.assertEqual(self.alert.baseline_date, "2026-01-15")

    def test_提前量以放款日为基准可复算(self):
        """FR-POST-04：提前量 = 命中时点 - 放款日，与系统上线日无关。"""
        self.assertEqual(self.alert.lead_time_days, 45)

    def test_换放款日提前量随之变化(self):
        monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")
        monitor.register(view(baseline="2026-02-20"))
        monitor.start()
        alert = monitor.scan_once("2026-03-01")[0]
        self.assertEqual(alert.lead_time_days, 9)

    def test_证据快照含规则版本与字段来源(self):
        evidence = self.alert.evidence
        self.assertEqual(evidence["rule"]["version"], "1.0.0")
        self.assertEqual(evidence["facts"]["days_past_due"]["channel"], "api")
        self.assertEqual(evidence["facts"]["days_past_due"]["value"], 45)

    def test_证据快照不可被后续覆盖(self):
        """FR-POST-03：事实后来变了，命中当时的快照不能变。"""
        before = self.alert.evidence["facts"]["days_past_due"]["value"]
        new_view = view(facts={"days_past_due": 999})
        self.monitor.register(new_view)
        self.monitor.scan_once("2026-04-01")
        self.assertEqual(self.alert.evidence["facts"]["days_past_due"]["value"], before)

    def test_证据快照写入即失败(self):
        with self.assertRaises(TypeError):
            self.alert.evidence["facts"] = {}

    def test_预警等级由规则严重度映射(self):
        self.assertEqual(self.alert.level, AlertLevel.HIGH)

    def test_重复扫描不重复预警(self):
        """常驻循环会反复扫描，不去重会刷出重复预警。"""
        again = self.monitor.scan_once("2026-04-01")
        self.assertEqual(again, ())
        self.assertEqual(len(self.monitor.alerts), 2)

    def test_预警可序列化(self):
        d = self.alert.to_dict()
        self.assertEqual(d["rule_id"], "R-PM-001")
        self.assertEqual(d["lead_time_days"], 45)
        self.assertIn("evidence", d)