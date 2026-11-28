# -*- coding: utf-8 -*-
"""切片 8.1：评测框架与 Gate 判定。

本组断言分四层：

    1. **加载与派生**——金标读进来对不对，三个评测面的规模对不对；
    2. **跑批**——真跑管线（切片 1/2/3/4 的实现）与金标逐件比对；
    3. **指标**——每条指标的值与状态，尤其是**未评测项不许冒充达标**；
    4. **Gate**——一票否决规则、未评测即不上线、人工事实项不代签。

第 3、4 层是本切片的核心纪律：**没测到 ≠ 达标**。
断言里对「未评测」的检查比「通过」的检查还多，因为后者最容易自欺。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

from finguard.agents.auditor import Auditor
from finguard.eval.gates import GATE_SPECS, evaluate_gates, overall_verdict
from finguard.eval.loader import (
    FACT_CATEGORIES,
    decision_factset,
    derive_eval_sets,
    fact_routes,
    load_gold,
)
from finguard.eval.metrics import Metric, MetricSet, compute_metric_set
from finguard.eval.runner import (
    load_injection_cases,
    replay_case,
    replay_fact_layer,
    run_evaluation,
    write_report,
)
from finguard.facts.extract import RouteResult
from finguard.facts.models import Confidence
from finguard.rules.engine import RuleEngine
from finguard.rules.registry import RuleStore
from finguard.rules.seeds import seed_rules


class EvalFixture(unittest.TestCase):
    """共用的跑批夹具——跑一次，全组复用。"""

    @classmethod
    def setUpClass(cls):
        cls.gold = load_gold()
        cls.store = RuleStore(seed_rules())
        cls.engine = RuleEngine()
        cls.auditor = Auditor()
        cls.sets = derive_eval_sets(cls.gold)
        cls.runs = tuple(
            replay_case(case, cls.store, cls.engine, cls.auditor)
            for case in cls.gold.cases
        )
        cls.fact_replays = tuple(
            replay_fact_layer(case) for case in cls.gold.cases if case.field_samples
        )
        cls.injection = load_injection_cases()
        cls.metrics = compute_metric_set(
            cls.runs,
            cls.fact_replays,
            cls.injection,
            cls.store,
            gold_cases={c.case_id: c for c in cls.gold.cases},
            fact_samples={c.case_id: c.field_samples for c in cls.gold.cases
                          if c.field_samples},
        )
        cls.report = run_evaluation()


# ---------------------------------------------------------------- 1 加载
class TestLoader(unittest.TestCase):
    def setUp(self):
        self.gold = load_gold()
        self.sets = derive_eval_sets(self.gold)

    def test_金标三十件且评测集版本已登记(self):
        self.assertEqual(len(self.gold.cases), 30)
        self.assertTrue(self.gold.gold_version)
        self.assertTrue(self.gold.guide_version)
        self.assertEqual(self.gold.annotator, "agent-b（独立标注）")
        self.assertEqual(self.gold.eval_set, "EVAL-REV")

    def test_三个评测面派生规模(self):
        sizes = self.sets.sizes
        self.assertEqual(sizes["EVAL-REV"], 30)
        # 每件 5 条规则 → 150 个版本对
        self.assertEqual(sizes["EVAL-VER"], 150)
        self.assertEqual(sizes["EVAL-FACT"], 14)

    def test_版本对覆盖全部规则且含须人工判定(self):
        needs_human = [p for p in self.sets.ver if p.needs_human]
        self.assertGreater(len(needs_human), 0)
        for pair in self.sets.ver:
            if pair.needs_human:
                self.assertIn(pair.expected_reason_code,
                              ("NO_VERSION_MATCH", "AMBIGUOUS_VERSION"))
            else:
                self.assertTrue(pair.expected_version)

    def test_已裁决事实集全部高置信(self):
        for case in self.gold.cases:
            factset = decision_factset(case)
            for name, field in factset.fields.items():
                self.assertIs(field.confidence, Confidence.HIGH, name)

    def test_两路重建按样本类别分流(self):
        by_case = {c.case_id: c for c in self.gold.cases}
        # 双路一致：两路同值
        route_a, route_b = fact_routes(by_case["GOLD-REV-001"])
        self.assertEqual(route_a.values["monthly_revenue"], 800000)
        self.assertEqual(route_b.values["monthly_revenue"], 800000)
        # 双路不一致：两路异值（取自样本登记的 channel_a/b_value）
        route_a, route_b = fact_routes(by_case["GOLD-REV-014"])
        self.assertEqual(route_a.values["monthly_revenue"], 640000)
        self.assertEqual(route_b.values["monthly_revenue"], 615000)
        # 跨渠道缺失：B 路没有该字段
        route_a, route_b = fact_routes(by_case["GOLD-REV-022"])
        self.assertIn("industry", route_a.values)
        self.assertNotIn("industry", route_b.values)

    def test_未知样本类别直接报错(self):
        from finguard.eval.loader import check_fact_category
        with self.assertRaises(ValueError):
            check_fact_category("GOLD-REV-001", "自创类别")
        for category in FACT_CATEGORIES:
            self.assertEqual(
                check_fact_category("GOLD-REV-001", category), category)

    def test_样本类别是封闭枚举(self):
        for case in self.gold.cases:
            for sample in case.field_samples:
                self.assertIn(sample.category, FACT_CATEGORIES)


# ---------------------------------------------------------------- 2 跑批
class TestReplay(EvalFixture):
    def test_三键绑定与金标逐件一致(self):
        for run in self.runs:
            case = self.gold.by_id(run.case_id)
            for binding in case.bindings:
                got = run.binding_map.get(binding.rule_id)
                self.assertEqual(got, (binding.version, binding.reason_code),
                                 "%s / %s" % (run.case_id, binding.rule_id))

    def test_覆盖度与金标PRD口径一致(self):
        for run in self.runs:
            case = self.gold.by_id(run.case_id)
            self.assertEqual(
                {
                    "judged": run.coverage.judged,
                    "not_applicable": run.coverage.not_applicable,
                    "unresolved": run.coverage.unresolved,
                    "total": run.coverage.total,
                },
                dict(case.coverage_prd),
                run.case_id,
            )
            self.assertTrue(run.coverage.is_consistent, run.case_id)

    def test_意见条数等于金标已判定条数(self):
        for run in self.runs:
            case = self.gold.by_id(run.case_id)
            self.assertEqual(len(run.opinions), case.expected_judged, run.case_id)

    def test_每条意见引用三件套齐全且可回溯(self):
        for run in self.runs:
            for op in run.opinions:
                self.assertTrue(op.rule_id)
                self.assertTrue(op.rule_version)
                self.assertTrue(op.fact_refs)
                self.assertTrue(op.fact_citations)
                cited = {c.field_path for c in op.fact_citations}
                self.assertEqual(cited, set(op.fact_refs))
                for citation in op.fact_citations:
                    self.assertTrue(citation.source_channel)

    def test_须人工判定的规则不得出硬判定(self):
        for run in self.runs:
            case = self.gold.by_id(run.case_id)
            for binding in case.bindings:
                if not binding.needs_human:
                    continue
                self.assertIsNone(run.evaluation_of(binding.rule_id))
                self.assertFalse(
                    [op for o in run.opinions if o.rule_id == binding.rule_id])

    def test_事实层重放把低置信字段拦在判定链外(self):
        for replay in self.fact_replays:
            self.assertEqual(set(replay.ready) & set(replay.blocked), set())
            for name in replay.blocked:
                field = replay.merged.get(name)
                if field is not None:
                    self.assertIs(field.confidence, Confidence.LOW, name)


# ---------------------------------------------------------------- 3 指标
class TestMetrics(EvalFixture):
    def test_硬指标全部达标(self):
        for metric_id in ("Q-01", "Q-02", "Q-03", "Q-04", "Q-05",
                          "Q-06", "Q-07", "Q-09", "Q-10"):
            metric = self.metrics.get(metric_id)
            self.assertIsNotNone(metric, metric_id)
            self.assertEqual(metric.status, "pass",
                             "%s 实际 %s（%s）" % (metric_id, metric.actual_text(),
                                                  metric.note))

    def test_安全硬指标达标(self):
        self.assertEqual(self.metrics.get("SEC-01").value, 0)
        self.assertEqual(self.metrics.get("SEC-02").value, 0)
        self.assertEqual(self.metrics.get("SEC-05").value, 1.0)

    def test_未评测项必须是未评测而不是达标(self):
        for metric_id in ("Q-08", "P-10"):
            metric = self.metrics.get(metric_id)
            self.assertEqual(metric.status, "not_evaluated", metric_id)
            self.assertIsNone(metric.value)
            self.assertTrue(metric.note.strip(), metric_id)
            self.assertIn("不可测", metric.note)

    def test_每条指标都有口径与PRD依据(self):
        for metric in self.metrics.metrics.values():
            self.assertTrue(metric.definition.strip(), metric.metric_id)
            self.assertTrue(metric.basis.strip(), metric.metric_id)
            self.assertTrue(metric.name.strip(), metric.metric_id)

    def test_注入样本零漏拦零误拦(self):
        injected = [i for i in self.injection if i.expect_isolated]
        normal = [i for i in self.injection if not i.expect_isolated]
        self.assertEqual(len(injected), 6)
        self.assertEqual(len(normal), 6)
        for item in injected:
            self.assertTrue(item.actual_isolated, item.sample_id)
        for item in normal:
            self.assertFalse(item.actual_isolated,
                             "%s 被误拦：%s" % (item.sample_id, item.reasons))

    def test_指标差异明细为空(self):
        # 首批金标与管线口径完全一致——有差异必须列出来，不许悄悄吞掉
        for metric_id, details in self.metrics.details.items():
            self.assertEqual(details, [], "%s 有未列出的差异：%s" % (metric_id, details[:3]))


# ---------------------------------------------------------------- 4 Gate
class TestGates(EvalFixture):
    def test_GATE1正确性通过(self):
        gates = {g.gate_id: g for g in evaluate_gates(self.metrics)}
        self.assertEqual(gates["GATE-1"].status, "pass")

    def test_未评测的一票否决Gate不得判通过(self):
        gates = {g.gate_id: g for g in evaluate_gates(self.metrics)}
        for gate_id in ("GATE-2", "GATE-3", "GATE-4"):
            self.assertEqual(gates[gate_id].status, "not_evaluated", gate_id)
            self.assertFalse(gates[gate_id].passed, gate_id)

    def test_总判定为不上线且不进入下一阶段(self):
        gates = evaluate_gates(self.metrics)
        verdict = overall_verdict(gates)
        self.assertEqual(verdict["上线判定"], "不上线")
        self.assertEqual(verdict["下一阶段判定"], "不进入下一阶段")
        self.assertIn("GATE-2", verdict["一票否决未评测"])

    def test_一票否决项不达标即不上线(self):
        ms = MetricSet()
        for metric in self.metrics.metrics.values():
            ms.add(metric)
        bad = ms.get("Q-02")
        ms.add(Metric(
            metric_id="Q-02", name=bad.name, comparator=bad.comparator,
            target=bad.target, value=0.9, numerator=135, denominator=150,
            status="fail", definition=bad.definition, basis=bad.basis,
        ))
        verdict = overall_verdict(evaluate_gates(ms))
        self.assertEqual(verdict["上线判定"], "不上线")
        self.assertIn("GATE-1", verdict["一票否决不达标"])

    def test_人工事实项不代签且可由人确认(self):
        gates = {g.gate_id: g for g in evaluate_gates(self.metrics)}
        item = next(i for i in gates["GATE-3"].items if i.metric_id is None)
        self.assertEqual(item.status, "not_evaluated")
        gates = {g.gate_id: g for g in evaluate_gates(
            self.metrics, manual_flags={"amendment_double_version": True})}
        item = next(i for i in gates["GATE-3"].items if i.metric_id is None)
        self.assertEqual(item.status, "pass")

    def test_Gate定义与PRD一致(self):
        ids = [s.gate_id for s in GATE_SPECS]
        self.assertEqual(ids, ["GATE-%d" % i for i in range(1, 9)])
        blocking = [s.gate_id for s in GATE_SPECS if s.blocking]
        self.assertEqual(blocking, ["GATE-1", "GATE-2", "GATE-3", "GATE-4"])


# ---------------------------------------------------------------- 5 报告
class TestReport(EvalFixture):
    def test_报告可序列化且结构完整(self):
        data = json.loads(self.report.to_json())
        for key in ("generated_at", "gold", "metrics", "gates", "verdict",
                    "cases", "fact_replays", "injection", "observations"):
            self.assertIn(key, data)
        self.assertEqual(len(data["cases"]), 30)
        self.assertEqual(len(data["gates"]), 8)

    def test_报告写盘后可读回(self):
        tmp = tempfile.mkdtemp(prefix="finguard-eval-")
        try:
            path = write_report(self.report, tmp)
            self.assertTrue(os.path.exists(path))
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["gold"]["gold_version"], self.gold.gold_version)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_报告标注样本量为口径试算(self):
        warnings = self.report.gold["sample_size_warning"]
        self.assertTrue(warnings)
        for text in warnings:
            self.assertIn("口径试算", text)

    def test_报告记录观察事项(self):
        self.assertTrue(self.report.observations)


if __name__ == "__main__":
    unittest.main()
