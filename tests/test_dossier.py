"""切片 6 · 六层卷宗与两档齐全性断言（PRD 8.9、FR-GATE-05、P-10）。

核心主张：**缺任一层即判不完整，不完整的卷宗不可用于监管取证。**
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.agents.harness.gates import build_gates
from finguard.facts.extract import RouteResult
from finguard.facts.layer import FactLayer
from finguard.facts.models import SourceChannel
from finguard.storage.dossier import (
    Completeness,
    Dossier,
    DossierLayer,
    content_hash,
    disposition_layer_payload,
    documents_layer_payload,
    facts_layer_payload,
    opinions_layer_payload,
    rules_layer_payload,
    signatures_layer_payload,
)


def route(name, values):
    return RouteResult(route=name, channel=SourceChannel.OCR, values=dict(values))


class TestLayerInventory(unittest.TestCase):
    def test_六层且顺序与PRD一致(self):
        self.assertEqual(len(list(DossierLayer)), 6)
        self.assertEqual(
            tuple(l.value for l in DossierLayer),
            (
                "L1_事实层快照",
                "L2_规则版本快照",
                "L3_依据文档快照",
                "L4_审查意见层",
                "L5_人审签核层",
                "L6_处置与贷后闭环层",
            ),
        )

    def test_两档齐全性枚举(self):
        self.assertEqual(len(list(Completeness)), 2)

    def test_写入非法层被拒(self):
        with self.assertRaises(ValueError):
            Dossier("FG-1").write("L7", {})


class TestCompleteness(unittest.TestCase):
    def _full(self) -> Dossier:
        d = Dossier("FG-1", clock=lambda: "2026-11-26T10:00:00")
        for layer in DossierLayer:
            d.write(layer, {"占位": layer.value})
        return d

    def test_六层齐全判完整(self):
        result = self._full().verify()
        self.assertIs(result.completeness, Completeness.COMPLETE)
        self.assertTrue(result.ok)
        self.assertTrue(result.usable_for_audit)

    def test_缺任一层判不完整(self):
        d = self._full()
        d._layers.pop(DossierLayer.L3)          # 白盒模拟该层未写入
        result = d.verify()
        self.assertIs(result.completeness, Completeness.INCOMPLETE)
        self.assertEqual(result.missing_layers, (DossierLayer.L3,))
        self.assertFalse(result.usable_for_audit)

    def test_层内容被改判被篡改(self):
        d = self._full()
        d._layers[DossierLayer.L1].payload["占位"] = "被人改过"
        result = d.verify()
        self.assertEqual(result.tampered_layers, (DossierLayer.L1,))
        self.assertIs(result.completeness, Completeness.INCOMPLETE)
        self.assertFalse(result.usable_for_audit)

    def test_层的自校验(self):
        d = self._full()
        snap = d.read(DossierLayer.L2)
        self.assertTrue(snap.verify_self())
        self.assertEqual(snap.content_hash, content_hash(snap.payload))

    def test_覆盖写后哈希刷新(self):
        d = self._full()
        old = d.read(DossierLayer.L4).content_hash
        d.write(DossierLayer.L4, {"占位": "第二版"})
        new = d.read(DossierLayer.L4).content_hash
        self.assertNotEqual(old, new)
        self.assertTrue(d.verify().ok)

    def test_序列化往返后仍完整(self):
        d = self._full()
        again = Dossier.from_dict(d.to_dict())
        self.assertTrue(again.verify().ok)
        self.assertEqual(again.case_id, "FG-1")

    def test_读取顺序按PRD(self):
        d = Dossier("FG-1")
        for layer in reversed(list(DossierLayer)):
            d.write(layer, {"x": 1})
        self.assertEqual([s.layer for s in d.layers()], list(DossierLayer))


class TestLayerPayloads(unittest.TestCase):
    def test_L1含取值来源通道与置信度(self):
        a = {"monthly_revenue": 820000, "declared_revenue": 1000000}
        factset, queue = FactLayer().build(route("vote", a), route("rule_check", a))
        payload = facts_layer_payload(factset)
        names = [f["field_path"] for f in payload["fields"]]
        self.assertIn("monthly_revenue", names)
        item = [f for f in payload["fields"] if f["field_path"] == "monthly_revenue"][0]
        self.assertEqual(item["value"], 820000)
        self.assertEqual(item["confidence"], "高")
        self.assertIn("ocr", item["source_channels"])

    def test_L1含冲突裁决人与时间(self):
        a = {"monthly_revenue": 820000}
        b = {"monthly_revenue": 615000}
        factset, queue = FactLayer().build(route("vote", a), route("rule_check", b))
        pending = facts_layer_payload(factset)["pending_conflicts"]
        self.assertEqual(len(pending), 1)
        self.assertIn("candidates", pending[0])
        self.assertIn("adjudicator", pending[0])

    def test_L2含版本与绑定依据(self):
        payload = rules_layer_payload(
            [{"rule_id": "R-CR-013", "version": "3.2.0", "binding_basis": "三键唯一命中"}],
            [{"rule_id": "R-CR-021", "unresolved_reason_code": "NO_VERSION_MATCH"}],
        )
        self.assertEqual(payload["rule_binding"][0]["version"], "3.2.0")
        self.assertEqual(payload["unresolved"][0]["unresolved_reason_code"], "NO_VERSION_MATCH")

    def test_L3含出处与原文(self):
        payload = documents_layer_payload(
            [{"doc_id": "D1", "chunk_id": "C1", "source_ref": "《制度》第4条",
              "quote": "原文片段", "published_at": "2025-07-01"}]
        )
        item = payload["citations"][0]
        self.assertEqual(item["chunk_id"], "C1")
        self.assertEqual(item["quote"], "原文片段")
        self.assertEqual(item["published_at"], "2025-07-01")

    def test_L4含意见与AgentSpec版本(self):
        payload = opinions_layer_payload([], {"judged": 1}, spec_version="auditor-1.0")
        self.assertEqual(payload["producer_spec_version"], "auditor-1.0")
        self.assertEqual(payload["coverage_summary"]["judged"], 1)

    def test_L5含三处闸门与阶段轨迹(self):
        gates = build_gates()
        payload = signatures_layer_payload(gates, ["S1", "S2", "S3"])
        self.assertEqual(set(payload["gates"]), {"GATE-A", "GATE-B", "GATE-C"})
        self.assertEqual(payload["stage_trail"], ["S1", "S2", "S3"])

    def test_L6含处置决定与预警核查(self):
        payload = disposition_layer_payload(
            {"action": "调整关注", "by": "u-003"},
            alerts=[{"rule_id": "R-PM-001"}],
            verifications=[{"conclusion": "已核实"}],
        )
        self.assertEqual(payload["disposition"]["action"], "调整关注")
        self.assertEqual(len(payload["alerts"]), 1)
        self.assertEqual(len(payload["verifications"]), 1)