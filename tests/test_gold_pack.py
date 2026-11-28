# -*- coding: utf-8 -*-
"""切片 8.0 · 金标任务包与双盲比对断言。"""
from __future__ import annotations

import io
import json
import os
import unittest

from finguard.eval.gold import (
    LABEL_FIELDS,
    LABEL_SCALES,
    Annotation,
    AnnotationError,
    agreement_report,
    compare_annotations,
    validate_annotation,
)
from finguard.eval.synth import generate_cases
from finguard.rules.seeds import RULES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_B = os.path.join(ROOT, "data", "gold", "pack-b")
LABELS = os.path.join(ROOT, "data", "gold", "labels")


def read_jsonl(path):
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestTaskPackSeparation(unittest.TestCase):
    """任务包与技术真值必须分离——裁判看不到答案。"""

    def setUp(self):
        self.materials = read_jsonl(os.path.join(PACK_B, "materials.jsonl"))
        self.truth = read_jsonl(os.path.join(PACK_B, "truth.jsonl"))

    def test_任务包不含任何真值字段(self):
        forbidden = ("facts", "rules_truth", "coverage", "verdict", "reason_code",
                     "ground_truth", "hit_label")
        for pack in self.materials:
            for key in forbidden:
                self.assertNotIn(key, pack, "任务包泄漏真值字段 %s" % key)

    def test_任务包与真值案件号一一对应(self):
        self.assertEqual([p["case_id"] for p in self.materials],
                         [t["case_id"] for t in self.truth])

    def test_任务包含规则清单(self):
        """裁判要判断规则适用性，必须知道有哪些规则及其适用范围。"""
        for pack in self.materials:
            self.assertIn("rules", pack)
            for rule in pack["rules"]:
                for key in ("rule_id", "product_scope", "org_scope", "required_facts",
                            "versions", "severity" if "severity" in rule else "versions"):
                    self.assertIn(key, rule)

    def test_材料含三键(self):
        """产品类型 / 机构层级 / 经办日期——缺一个就无法判断规则适用性。"""
        for pack in self.materials:
            text = "\n".join(m["text"] for m in pack["materials"])
            for token in ("产品类型：", "机构层级：", "经办日期："):
                self.assertIn(token, text, "材料缺 %s" % token)

    def test_盲标批次标记(self):
        with io.open(os.path.join(PACK_B, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertTrue(manifest.get("blind_batch"))
        self.assertIn("blind_note", manifest)
        self.assertTrue(manifest["rules_fingerprint"].startswith("sha256:"))


class TestReproducible(unittest.TestCase):
    def test_同seed生成同一批材料(self):
        rules = sorted(RULES, key=lambda r: (r.rule_id, r.version))
        a = generate_cases(rules, count=3, seed=777, case_prefix="FG-T")
        b = generate_cases(rules, count=3, seed=777, case_prefix="FG-T")
        self.assertEqual([c.case_id for c in a], [c.case_id for c in b])
        self.assertEqual([m.text for c in a for m in c.materials],
                         [m.text for c in b for m in c.materials])

    def test_覆盖度三类可同时出现(self):
        """已判定 / 不适用 / 未判定（版本重叠）三条分支都要有样本。"""
        rules = sorted(RULES, key=lambda r: (r.rule_id, r.version))
        cases = generate_cases(rules, count=12, seed=4242, case_prefix="FG-C")
        coverages = [dict(c.coverage) for c in cases]
        has_na = any(c["not_applicable"] > 0 for c in coverages)
        has_unresolved = any(c["unresolved"] > 0 for c in coverages)
        has_judged = any(c["judged"] > 0 for c in coverages)
        self.assertTrue(has_na and has_unresolved and has_judged, coverages)


class TestAnnotationContract(unittest.TestCase):
    def _labels(self):
        return {f: LABEL_SCALES[f][0] for f in LABEL_FIELDS}

    def test_五个字段且枚举封闭(self):
        self.assertEqual(len(LABEL_FIELDS), 5)
        for name, scale in LABEL_SCALES.items():
            self.assertTrue(scale)

    def test_合法标注通过校验(self):
        validate_annotation(Annotation("C1", "a", self._labels()))

    def test_缺字段被拒(self):
        labels = self._labels()
        labels.pop("reason_quality")
        with self.assertRaises(AnnotationError):
            validate_annotation(Annotation("C1", "a", labels))

    def test_非法取值被拒(self):
        labels = self._labels()
        labels["reason_quality"] = "还行吧"
        with self.assertRaises(AnnotationError):
            validate_annotation(Annotation("C1", "a", labels))

    def test_缺标注人被拒(self):
        with self.assertRaises(AnnotationError):
            validate_annotation(Annotation("C1", "", self._labels()))

    def test_无对象时用不适用(self):
        """无命中意见时三个字段无对象，枚举必须留「不适用」这个出口。"""
        for name in ("reason_quality", "severity_appropriate", "is_false_positive"):
            self.assertIn("不适用", LABEL_SCALES[name])


class TestBlindComparison(unittest.TestCase):
    def _annotation(self, case_id, annotator, override=None):
        labels = {f: LABEL_SCALES[f][0] for f in LABEL_FIELDS}
        labels.update(override or {})
        return Annotation(case_id, annotator, labels)

    def test_一致与分歧都能识别(self):
        a = self._annotation("C1", "impl")
        b = self._annotation("C1", "review")
        self.assertTrue(compare_annotations(a, b)["agree"])
        b2 = self._annotation("C1", "review", {"rule_applicability": "不同意"})
        result = compare_annotations(a, b2)
        self.assertFalse(result["agree"])
        self.assertEqual(len(result["diffs"]), 1)

    def test_一致率与分歧清单(self):
        a = self._annotation("C1", "impl")
        b = self._annotation("C1", "review", {"severity_appropriate": "偏高"})
        report = agreement_report([a], [b])
        self.assertEqual(report["cases_compared"], 1)
        self.assertEqual(report["cases_agree"], 0)
        self.assertEqual(report["diff_count"], 1)
        self.assertIsNotNone(report["field_agreement_rate"])

    def test_不能比对不同案件(self):
        with self.assertRaises(AnnotationError):
            compare_annotations(self._annotation("C1", "a"), self._annotation("C2", "b"))

    def test_已交付的标注文件合法(self):
        path = os.path.join(LABELS, "impl-agent.jsonl")
        rows = read_jsonl(path)
        self.assertTrue(rows)
        for row in rows:
            validate_annotation(Annotation.from_dict(row))
        ids = [r["case_id"] for r in rows]
        pack_ids = {p["case_id"] for p in
                    read_jsonl(os.path.join(PACK_B, "materials.jsonl"))}
        self.assertTrue(set(ids).issubset(pack_ids), "标注引用了任务包里没有的案件")