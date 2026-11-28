"""B 线金标 · 首批 30 件的独立标注与管线交叉校验。

金标由评审方 agent-b **独立手工推演**（读种子规则与引擎语义后落 JSON），
本组断言把它与确定性管线（RuleStore.bind + RuleEngine.evaluate）交叉校验：

    · 一致   → 金标与实现口径相同，冻结为回归基线
    · 分歧   → 三类之一：金标笔误（改金标）/ 实现缺陷（开评审意见）/
               口径定义分歧（进 data/gold/ARBITRATION.md 等仲裁）

覆盖度按 **PRD 口径**计算（未判定含绑定级须人工判定，见 ARBITRATION.md A-01），
与实现侧 Auditor 的 coverage 口径差异已在仲裁日志登记，不在本组断言内比较。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from datetime import date

from finguard.domain.enums import CoverageFlag, Verdict
from finguard.rules.engine import RuleEngine
from finguard.rules.registry import RuleStore
from finguard.rules.seeds import seed_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "data", "gold", "eval_rev_batch1.json")

BUCKET_QUOTA = {"对公信贷": 15, "小额经营贷": 9, "跨版本过渡": 3, "边界/冲突": 3}
FACT_CATEGORIES = ("双路一致", "双路不一致", "跨渠道缺失", "勾稽冲突")


def load_gold():
    with io.open(GOLD, encoding="utf-8") as fh:
        return json.load(fh)


def parse_date(text):
    y, m, d = (int(x) for x in text.split("-"))
    return date(y, m, d)


class TestGoldBatchSchema(unittest.TestCase):
    def setUp(self):
        self.gold = load_gold()
        self.cases = self.gold["cases"]

    def test_首批三十件且桶配比符合PRD(self):
        self.assertEqual(len(self.cases), 30)
        counts = {}
        for c in self.cases:
            counts[c["bucket"]] = counts.get(c["bucket"], 0) + 1
        self.assertEqual(counts, BUCKET_QUOTA)

    def test_案件号唯一且带标注溯源(self):
        ids = [c["case_id"] for c in self.cases]
        self.assertEqual(len(set(ids)), len(ids))
        for c in self.cases:
            self.assertEqual(c["annotator"], "agent-b")
            self.assertEqual(c["guide_version"], "v1")
            self.assertTrue(c["note"])

    def test_每条绑定与判定都落在种子规则集内(self):
        store = RuleStore(seed_rules())
        known = {r.rule_id for r in seed_rules()}
        for c in self.cases:
            for item in c["gold"]["binding"]:
                self.assertIn(item["rule_id"], known, c["case_id"])
            for item in c["gold"]["verdicts"]:
                self.assertIn(item["rule_id"], known, c["case_id"])
            # 绑定表必须覆盖全部 rule_id（含须人工判定），不多不少
            self.assertEqual(
                sorted(i["rule_id"] for i in c["gold"]["binding"]),
                sorted(known), c["case_id"])

    def test_EVAL_FACT子集字段样本类别齐备(self):
        subset = [c for c in self.cases if c["eval_fact_subset"]]
        self.assertGreaterEqual(len(subset), 10)
        cats = set()
        for c in subset:
            for s in c["field_samples"]:
                self.assertIn(s["category"], FACT_CATEGORIES)
                cats.add(s["category"])
                if s["category"] == "双路一致":
                    self.assertEqual(c["facts"][s["field"]], s["value"])
        self.assertEqual(cats, set(FACT_CATEGORIES))


class TestGoldAgainstPipeline(unittest.TestCase):
    """金标 vs 确定性管线：绑定、判定、严重度、引用字段、PRD 覆盖度。"""

    def setUp(self):
        self.store = RuleStore(seed_rules())
        self.engine = RuleEngine()
        self.cases = load_gold()["cases"]

    def _bind(self, triple):
        return self.store.bind(
            parse_date(triple["business_date"]),
            triple["product_type"],
            triple["org_level"],
        )

    def test_三键绑定与金标逐件一致(self):
        for c in self.cases:
            result = self._bind(c["triple_key"])
            got = {}
            for b in result.bindings:
                got[b.rule_id] = (b.version, None)
            for u in result.unresolved:
                got[u.rule_id] = (None, u.reason_code.value)
            want = {}
            for item in c["gold"]["binding"]:
                want[item["rule_id"]] = (item["version"], item["reason_code"])
            self.assertEqual(got, want, "%s 绑定分歧" % c["case_id"])

    def test_判定严重度与引用字段逐件一致(self):
        for c in self.cases:
            result = self._bind(c["triple_key"])
            by_key = {("%s@%s" % (b.rule_id, b.version)): b for b in result.bindings}
            rules = {("%s@%s" % (r.rule_id, r.version)): r for r in seed_rules()}
            for item in c["gold"]["verdicts"]:
                key = "%s@%s" % (item["rule_id"], item["version"])
                self.assertIn(key, by_key, "%s 金标判定了未绑定的规则" % c["case_id"])
                ev = self.engine.evaluate(rules[key], dict(c["facts"]))
                if ev.coverage is CoverageFlag.UNRESOLVED:
                    got = ("unresolved", ev.reason_code.value if ev.reason_code else None)
                elif ev.coverage is CoverageFlag.NOT_APPLICABLE:
                    got = ("not_applicable", None)
                else:
                    got = (ev.verdict.value, None)
                self.assertEqual(got, (item["verdict"], item["reason_code"]),
                                 "%s %s 判定分歧" % (c["case_id"], key))
                if item["verdict"] == "hit":
                    self.assertEqual(ev.severity.value, item["severity"],
                                     "%s %s 严重度分歧" % (c["case_id"], key))
                self.assertEqual(sorted(ev.fact_refs), item["fact_refs"],
                                 "%s %s 引用字段分歧" % (c["case_id"], key))

    def test_绑定且金标未列判定的规则必须不存在(self):
        """金标不得漏列任何已绑定规则的判定（漏列 = 覆盖度口径失真）。"""
        for c in self.cases:
            result = self._bind(c["triple_key"])
            bound = {("%s@%s" % (b.rule_id, b.version)) for b in result.bindings}
            listed = {("%s@%s" % (i["rule_id"], i["version"]))
                      for i in c["gold"]["verdicts"]}
            self.assertEqual(bound, listed, "%s 判定清单与绑定集不一致" % c["case_id"])

    def test_PRD口径覆盖度逐件一致(self):
        for c in self.cases:
            result = self._bind(c["triple_key"])
            rules = {("%s@%s" % (r.rule_id, r.version)): r for r in seed_rules()}
            judged = na = unresolved = 0
            for b in result.bindings:
                ev = self.engine.evaluate(
                    rules["%s@%s" % (b.rule_id, b.version)], dict(c["facts"]))
                if ev.coverage is CoverageFlag.UNRESOLVED:
                    unresolved += 1
                elif ev.coverage is CoverageFlag.NOT_APPLICABLE:
                    na += 1
                else:
                    judged += 1
            unresolved += len(result.unresolved)      # 绑定级须人工判定计入未判定
            total = len(result.bindings) + len(result.unresolved)
            self.assertEqual(
                c["gold"]["coverage_prd"],
                {"judged": judged, "not_applicable": na,
                 "unresolved": unresolved, "total": total},
                "%s 覆盖度分歧" % c["case_id"])

    def test_缺失事实声明与金标事实表自洽(self):
        for c in self.cases:
            for name in c["missing_facts"]:
                self.assertNotIn(name, c["facts"], c["case_id"])
            for item in c["gold"]["verdicts"]:
                if item["verdict"] == "unresolved":
                    self.assertEqual(item["reason_code"], "MISSING_FACT", c["case_id"])
                    for f in item["fact_refs"]:
                        if f not in c["facts"]:
                            self.assertIn(f, c["missing_facts"], c["case_id"])


if __name__ == "__main__":
    unittest.main()
