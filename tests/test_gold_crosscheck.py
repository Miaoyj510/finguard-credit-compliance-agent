"""A-01 裁决落地断言 + 标注人 A「系统输出作对照」的交叉校验断言。"""
from __future__ import annotations

import io
import json
import os
import unittest

from finguard.agents.auditor import Auditor
from finguard.domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from finguard.rules.engine import Evaluation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "data", "gold", "eval_rev_batch1.json")


def hit_eval():
    return Evaluation(
        rule_id="R-CR-013", rule_version="3.2.0",
        verdict=Verdict.HIT, coverage=CoverageFlag.JUDGED,
        reason="命中", reason_code=None,
        fact_refs=("monthly_revenue", "declared_revenue"),
        values=(("monthly_revenue", 820000), ("declared_revenue", 1000000)),
        severity=Severity.MAJOR, hit_label="申报营收与流水不符",
    )


class TestA01PrdCoverageCaliber(unittest.TestCase):
    """A-01 裁决：覆盖度按 PRD 口径——绑定级须人工判定计入未判定。"""

    def _build(self, bindings):
        from finguard.facts.models import Confidence, FactField, FactSet, SourceChannel
        factset = FactSet(fields={
            "monthly_revenue": FactField("monthly_revenue", 820000, Confidence.HIGH,
                                        channels=(SourceChannel.OCR,)),
            "declared_revenue": FactField("declared_revenue", 1000000, Confidence.HIGH,
                                         channels=(SourceChannel.OCR,)),
        })
        from finguard.domain.rule import Rule
        from datetime import date
        rule = Rule(
            rule_id="R-CR-013", version="3.2.0",
            effective_from=date(2025, 7, 1), effective_to=None,
            product_scope=("小额经营贷",), org_scope=("*",),
            severity=Severity.MAJOR,
            dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('x')",
            source_ref="《制度》第四条",
        )
        return Auditor().build([hit_eval()], factset, {"R-CR-013@3.2.0": rule},
                               bound_rule_total=1, unresolved_bindings=bindings)

    def test_未传绑定级时保持旧行为(self):
        """不传 unresolved_bindings 时向后兼容（切片 4 既有断言不改）。"""
        coverage = self._build([]).coverage
        self.assertEqual(coverage.unresolved, 0)
        self.assertEqual(coverage.total, 1)
        self.assertEqual(coverage.binding_unresolved, 0)
        self.assertTrue(coverage.is_consistent)

    def test_绑定级须人工判定计入未判定(self):
        """PRD 10.2.1 线框场景：2 条 NO_VERSION_MATCH -> 未判定 2。"""
        from finguard.rules.registry import UnresolvedRule
        bindings = [
            UnresolvedRule(rule_id="R-CR-021",
                           reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                           detail="产品范围不适用"),
            UnresolvedRule(rule_id="R-CR-030",
                           reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                           detail="产品范围不适用"),
        ]
        coverage = self._build(bindings).coverage
        self.assertEqual(coverage.unresolved, 2)
        self.assertEqual(coverage.binding_unresolved, 2)
        self.assertEqual(coverage.total, 3)          # 1 已判定 + 2 绑定级未判定
        self.assertEqual(coverage.judged + coverage.not_applicable
                         + coverage.unresolved, coverage.total)

    def test_未判定原因码可列(self):
        from finguard.rules.registry import UnresolvedRule
        bindings = [UnresolvedRule(rule_id="R-CR-021",
                                    reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                                    detail="产品范围不适用")]
        coverage = self._build(bindings).coverage
        codes = [e.reason_code.value for e in coverage.unresolved_entries]
        self.assertIn("NO_VERSION_MATCH", codes)

    def test_交叉校验改口径后仍成立(self):
        """bound_rule_total + binding_unresolved == total。"""
        from finguard.rules.registry import UnresolvedRule
        bindings = [UnresolvedRule(rule_id="R-CR-021",
                                    reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                                    detail="x")]
        coverage = self._build(bindings).coverage
        self.assertEqual(coverage.bound_rule_total + coverage.binding_unresolved,
                         coverage.total)
        self.assertTrue(coverage.is_consistent)

    def test_交叉校验对不上时判不自洽(self):
        from finguard.rules.registry import UnresolvedRule
        bindings = [UnresolvedRule(rule_id="R-CR-021",
                                    reason_code=UnresolvedReasonCode.NO_VERSION_MATCH,
                                    detail="x")]
        from finguard.facts.models import Confidence, FactField, FactSet, SourceChannel
        from finguard.domain.rule import Rule
        from datetime import date
        factset = FactSet(fields={
            "monthly_revenue": FactField("monthly_revenue", 820000, Confidence.HIGH,
                                         channels=(SourceChannel.OCR,)),
            "declared_revenue": FactField("declared_revenue", 1000000, Confidence.HIGH,
                                          channels=(SourceChannel.OCR,)),
        })
        rule = Rule(
            rule_id="R-CR-013", version="3.2.0",
            effective_from=date(2025, 7, 1), effective_to=None,
            product_scope=("小额经营贷",), org_scope=("*",),
            severity=Severity.MAJOR,
            dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('x')",
            source_ref="《制度》第四条",
        )
        # bound_rule_total 故意给错（3），交叉校验必须判否
        coverage = Auditor().build([hit_eval()], factset, {"R-CR-013@3.2.0": rule},
                                   bound_rule_total=3, unresolved_bindings=bindings).coverage
        self.assertFalse(coverage.is_consistent)


class TestAnnotatorACrosscheck(unittest.TestCase):
    """标注人 A 提供系统输出作对照（规范书 v1 第 0 节）。"""

    def setUp(self):
        self.gold = json.load(io.open(GOLD, encoding="utf-8"))

    def test_金标文件结构完整(self):
        self.assertEqual(self.gold["eval_set"], "EVAL-REV")
        self.assertEqual(len(self.gold["cases"]), 30)
        for case in self.gold["cases"]:
            self.assertIn("triple_key", case)
            self.assertIn("gold", case)
            self.assertIn("coverage_prd", case["gold"])

    def test_桶配比符合规范书(self):
        quota = self.gold["bucket_quota"]
        counts = {}
        for case in self.gold["cases"]:
            counts[case["bucket"]] = counts.get(case["bucket"], 0) + 1
        self.assertEqual(counts, quota)

    def test_系统输出与金标逐件一致(self):
        """交叉校验：管线输出 vs 手工金标。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gold_crosscheck", os.path.join(ROOT, "scripts", "gold_crosscheck.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from finguard.rules.engine import RuleEngine
        from finguard.rules.registry import RuleStore
        from finguard.rules.seeds import RULES as CREDIT_RULES
        store = RuleStore(sorted(CREDIT_RULES, key=lambda r: (r.rule_id, r.version)))
        engine = RuleEngine()
        diffs = 0
        for case in self.gold["cases"]:
            sys_out = module.run_case(store, engine, case)
            diffs += len(module.compare(case, sys_out))
        self.assertEqual(diffs, 0, "系统输出与金标存在分歧，需走仲裁")