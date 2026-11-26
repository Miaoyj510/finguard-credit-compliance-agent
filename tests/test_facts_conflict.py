"""冲突裁决队列（PRD 6.2 FR-FACT-06、8.2 裁决队列）。"""
from __future__ import annotations

import unittest

from finguard.facts.conflict import ConflictItem, ConflictKind, ConflictQueue, ConflictStatus
from finguard.facts.models import Confidence, FactField, SourceChannel


def make_item(field="monthly_revenue", candidates=(820000, 615000)):
    return ConflictItem(
        conflict_id="low_confidence:%s" % field,
        field=field,
        kind=ConflictKind.LOW_CONFIDENCE,
        candidates=tuple(candidates),
        detail="两路取值不一致，须人工裁决",
    )


class TestResolve(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = ConflictQueue([make_item()])

    def test_裁决成功(self):
        item = self.queue.resolve("low_confidence:monthly_revenue", 820000, "与对账单核对一致", "张合规")
        self.assertIs(item.status, ConflictStatus.RESOLVED)
        self.assertEqual(item.chosen, 820000)
        self.assertEqual(item.reason, "与对账单核对一致")
        self.assertEqual(item.adjudicator, "张合规")

    def test_理由为空被拒(self):
        for reason in ("", "   ", None):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    self.queue.resolve("low_confidence:monthly_revenue", 820000, reason, "张合规")

    def test_裁决人为空被拒(self):
        with self.assertRaises(ValueError):
            self.queue.resolve("low_confidence:monthly_revenue", 820000, "理由", "")

    def test_裁决值不在候选内被拒(self):
        with self.assertRaises(ValueError):
            self.queue.resolve("low_confidence:monthly_revenue", 999999, "理由", "张合规")

    def test_已裁决不可重复裁决(self):
        self.queue.resolve("low_confidence:monthly_revenue", 820000, "理由", "张合规")
        with self.assertRaises(ValueError):
            self.queue.resolve("low_confidence:monthly_revenue", 615000, "改主意", "张合规")

    def test_未知冲突项报错(self):
        with self.assertRaises(KeyError):
            self.queue.resolve("low_confidence:不存在", 1, "理由", "张合规")


class TestQueueState(unittest.TestCase):
    def test_待裁决与已裁决分开(self):
        q = ConflictQueue([make_item("a", (1, 2)), make_item("b", (3, 4))])
        self.assertEqual(len(q.pending()), 2)
        q.resolve("low_confidence:a", 1, "理由", "张合规")
        self.assertEqual(len(q.pending()), 1)
        self.assertEqual(len(q.resolved()), 1)

    def test_重复添加被拒(self):
        q = ConflictQueue([make_item()])
        with self.assertRaises(ValueError):
            q.add(make_item())

    def test_裁决后回填为高置信(self):
        q = ConflictQueue([make_item()])
        fields = {
            "monthly_revenue": FactField(
                name="monthly_revenue", value=615000, confidence=Confidence.LOW,
                channels=(SourceChannel.OCR,), routes=("vote",), candidates=(820000, 615000),
            )
        }
        q.resolve("low_confidence:monthly_revenue", 820000, "以对账单为准", "张合规")
        out = q.apply_resolutions(fields)
        f = out["monthly_revenue"]
        self.assertIs(f.confidence, Confidence.HIGH)
        self.assertEqual(f.value, 820000)
        self.assertEqual(f.channels, (SourceChannel.ADJUDICATED,))
        self.assertIn("以对账单为准", f.note)

    def test_未裁决的字段保持原样(self):
        q = ConflictQueue([make_item()])
        fields = {
            "monthly_revenue": FactField(
                name="monthly_revenue", value=615000, confidence=Confidence.LOW,
                channels=(SourceChannel.OCR,),
            )
        }
        out = q.apply_resolutions(fields)
        self.assertIs(out["monthly_revenue"].confidence, Confidence.LOW)

    def test_可序列化(self):
        q = ConflictQueue([make_item()])
        d = q.get("low_confidence:monthly_revenue").to_dict()
        self.assertEqual(d["kind"], "low_confidence")
        self.assertEqual(d["status"], "待裁决")


if __name__ == "__main__":
    unittest.main()
