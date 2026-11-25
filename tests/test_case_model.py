"""三键与案件骨架校验（PRD 4.3 受理三键必填、8.4 CaseState）。

评审新增：三键是版本选择的唯一依据，任一 key 缺失或带空白都会让
「同一笔业务可复算」失效，必须在构造期拒绝。
"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.domain.case import CaseState, TripleKey
from finguard.rules.registry import RuleStore
from finguard.rules.seeds import seed_rules


class TestTripleKeyValidation(unittest.TestCase):
    def test_合法三键可构造(self):
        k = TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行")
        self.assertEqual(k.to_dict()["business_date"], "2026-10-28")

    def test_缺business_date被拒(self):
        with self.assertRaises(ValueError):
            TripleKey(None, "小额经营贷", "二级分行")

    def test_business_date非日期被拒(self):
        with self.assertRaises(ValueError):
            TripleKey("2026-10-28", "小额经营贷", "二级分行")

    def test_空白product_type被拒(self):
        with self.assertRaises(ValueError):
            TripleKey(date(2026, 10, 28), "   ", "二级分行")

    def test_空白org_level被拒(self):
        with self.assertRaises(ValueError):
            TripleKey(date(2026, 10, 28), "小额经营贷", "  ")


class TestBindCase(unittest.TestCase):
    """HANDOFF 关键设计点 5：bind_case 写回（评审前无断言覆盖）。"""

    def test_bind_case写回绑定与未判定(self):
        case = CaseState.create(
            "FG-20261114-000123",
            TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行"),
        )
        store = RuleStore(seed_rules())
        returned = store.bind_case(case)
        self.assertIs(returned, case)
        self.assertIn("R-CR-013", [b.rule_id for b in case.rule_binding])
        self.assertIn("R-CR-030", [u.rule_id for u in case.unresolved])
        self.assertEqual(case.to_dict()["triple_key"]["product_type"], "小额经营贷")

    def test_bind_case可复算(self):
        key = TripleKey(date(2025, 8, 1), "小额经营贷", "二级分行")
        store = RuleStore(seed_rules())
        a = store.bind_case(CaseState.create("FG-1", key)).to_dict()
        b = store.bind_case(CaseState.create("FG-1", key)).to_dict()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
