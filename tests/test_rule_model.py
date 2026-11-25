"""规则模型校验（PRD 6.1 FR-RAG-03：必填字段缺失即不允许入库）。"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.domain.enums import Severity
from finguard.domain.rule import Rule


def make_rule(**overrides) -> Rule:
    base = dict(
        rule_id="R-CR-013",
        version="3.2.0",
        effective_from=date(2025, 7, 1),
        effective_to=None,
        product_scope=("小额经营贷",),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('不符')",
        required_facts=("monthly_revenue", "declared_revenue"),
        source_ref="《信贷业务审查操作规程》第四条",
    )
    base.update(overrides)
    return Rule(**base)


class TestRuleValidation(unittest.TestCase):
    def test_合法规则可构造(self):
        r = make_rule()
        self.assertEqual(r.identity, "R-CR-013@3.2.0")

    def test_非法rule_id被拒(self):
        """格式为 R-<域>-<数字序号>；缺段、小写、非数字序号一律拒绝。"""
        for bad in ("CR-013", "R-cr-013", "", "X-1", "R-CR-", "R-CR-013A", "-CR-013"):
            with self.subTest(rule_id=bad):
                with self.assertRaises(ValueError):
                    make_rule(rule_id=bad)

    def test_合法rule_id格式(self):
        """序号位数不限，但域段必须全大写字母。"""
        for good in ("R-CR-013", "R-CR-13", "R-POST-007", "R-A-1"):
            with self.subTest(rule_id=good):
                self.assertEqual(make_rule(rule_id=good).rule_id, good)

    def test_缺version被拒(self):
        with self.assertRaises(ValueError):
            make_rule(version="")

    def test_缺source_ref被拒(self):
        """没有出处 = 复核时找不到依据，引用可回溯率直接归零。"""
        with self.assertRaises(ValueError):
            make_rule(source_ref="")

    def test_缺dsl被拒(self):
        with self.assertRaises(ValueError):
            make_rule(dsl="   ")

    def test_空适用范围被拒(self):
        with self.assertRaises(ValueError):
            make_rule(product_scope=())
        with self.assertRaises(ValueError):
            make_rule(org_scope=())

    def test_生效区间倒置被拒(self):
        with self.assertRaises(ValueError):
            make_rule(effective_from=date(2025, 7, 1), effective_to=date(2025, 7, 1))
        with self.assertRaises(ValueError):
            make_rule(effective_from=date(2025, 7, 1), effective_to=date(2024, 1, 1))


class TestRuleScope(unittest.TestCase):
    def test_产品范围通配与精确(self):
        r = make_rule(product_scope=("小额经营贷",))
        self.assertTrue(r.covers_product("小额经营贷"))
        self.assertFalse(r.covers_product("大额授信"))
        rw = make_rule(product_scope=("*",))
        self.assertTrue(rw.covers_product("任意产品"))

    def test_机构范围通配与精确(self):
        r = make_rule(org_scope=("一级分行",))
        self.assertTrue(r.covers_org("一级分行"))
        self.assertFalse(r.covers_org("二级分行"))
        rw = make_rule(org_scope=("*",))
        self.assertTrue(rw.covers_org("二级分行"))

    def test_生效区间左闭右开(self):
        r = make_rule(effective_from=date(2025, 7, 1), effective_to=date(2026, 1, 1))
        self.assertFalse(r.effective_on(date(2025, 6, 30)))
        self.assertTrue(r.effective_on(date(2025, 7, 1)))    # 起始日含
        self.assertTrue(r.effective_on(date(2025, 12, 31)))
        self.assertFalse(r.effective_on(date(2026, 1, 1)))   # 结束日不含

    def test_长期有效(self):
        r = make_rule(effective_from=date(2025, 7, 1), effective_to=None)
        self.assertTrue(r.effective_on(date(2099, 12, 31)))

    def test_to_dict可序列化(self):
        d = make_rule().to_dict()
        self.assertEqual(d["rule_id"], "R-CR-013")
        self.assertEqual(d["severity"], "重要")
        self.assertEqual(d["effective_to"], None)


if __name__ == "__main__":
    unittest.main()
