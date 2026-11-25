"""三键取版本（PRD 5.3、FR-RAG-04 / 05）——本项目最核心的一段逻辑。"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.domain.enums import UnresolvedReasonCode
from finguard.domain.rule import Rule
from finguard.rules.registry import RuleStore
from finguard.rules.seeds import seed_rules


class TestRuleStoreBinding(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RuleStore(seed_rules())

    # ---------- 正常路径 ----------
    def test_唯一命中取到现行版本(self):
        """2026-10-28 / 小额经营贷 / 二级分行 → R-CR-013 取 v3.2.0"""
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        self.assertEqual(r.version_of("R-CR-013"), "3.2.0")
        self.assertEqual(r.version_of("R-CR-021"), "1.4.1")

    def test_历史时点取到历史版本(self):
        """2024-06-30 → R-CR-013 取 v3.0.0，而不是当时的最新版"""
        r = self.store.bind(date(2024, 6, 30), "小额经营贷", "二级分行")
        self.assertEqual(r.version_of("R-CR-013"), "3.0.0")

    def test_生效边界当天取新版(self):
        """左闭右开：2025-07-01 取 v3.2.0，2025-06-30 取 v3.0.0"""
        self.assertEqual(
            self.store.bind(date(2025, 7, 1), "小额经营贷", "二级分行").version_of("R-CR-013"),
            "3.2.0",
        )
        self.assertEqual(
            self.store.bind(date(2025, 6, 30), "小额经营贷", "二级分行").version_of("R-CR-013"),
            "3.0.0",
        )

    # ---------- 须人工判定的两条分支 ----------
    def test_产品不适用则须人工判定(self):
        """R-CR-030 只适用大额授信 → 小额经营贷下必须进待定池"""
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        u = {x.rule_id: x for x in r.unresolved}
        self.assertIn("R-CR-030", u)
        self.assertIs(u["R-CR-030"].reason_code, UnresolvedReasonCode.NO_VERSION_MATCH)
        self.assertNotIn("R-CR-030", r.bound_rule_ids)

    def test_机构层级不适用则须人工判定(self):
        """R-CR-050 仅一级分行 → 二级分行下必须进待定池"""
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        u = {x.rule_id: x for x in r.unresolved}
        self.assertIn("R-CR-050", u)
        self.assertIs(u["R-CR-050"].reason_code, UnresolvedReasonCode.NO_VERSION_MATCH)

    def test_版本区间重叠则须人工判定并列出候选(self):
        """R-CR-041 两版本生效区间重叠于 2025-06-01 ~ 2025-12-31。

        窗口内取任一日期都必须判为须人工判定，并列出两个候选版本供人工裁定。

        注意重叠窗口是左闭右开 [2025-06-01, 2025-12-31)：2025-12-31 当天旧版已失效，
        只剩新版命中，属于唯一命中，不算冲突。
        """
        for day in (date(2025, 6, 1), date(2025, 8, 1), date(2025, 12, 30)):
            with self.subTest(business_date=day.isoformat()):
                r = self.store.bind(day, "小额经营贷", "二级分行")
                u = {x.rule_id: x for x in r.unresolved}
                self.assertIn("R-CR-041", u)
                self.assertIs(u["R-CR-041"].reason_code, UnresolvedReasonCode.AMBIGUOUS_VERSION)
                self.assertIn("R-CR-041@1.0.0", u["R-CR-041"].candidates)
                self.assertIn("R-CR-041@1.1.0", u["R-CR-041"].candidates)
                self.assertNotIn("R-CR-041", r.bound_rule_ids)

    def test_重叠窗口结束后取唯一新版(self):
        """2026-10-28 时旧版已失效 → R-CR-041 唯一命中 v1.1.0"""
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        self.assertEqual(r.version_of("R-CR-041"), "1.1.0")
        self.assertNotIn("R-CR-041", [u.rule_id for u in r.unresolved])

    # ---------- 不可让步的行为 ----------
    def test_绝不按最新版硬判(self):
        """没有唯一版本时，不能拿任何一版顶上——这是合规底线，不是效率问题。"""
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        for u in r.unresolved:
            self.assertNotIn(u.rule_id, r.bound_rule_ids)

    def test_绑定结果可复算(self):
        """同一三键两次调用结果完全一致（可复算要求）"""
        a = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行").to_dict()
        b = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行").to_dict()
        self.assertEqual(a, b)

    def test_同一rule_id只绑定一次(self):
        r = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        ids = [b.rule_id for b in r.bindings]
        self.assertEqual(len(ids), len(set(ids)))

    def test_不同三键得到不同版本(self):
        """三键必须真的参与选择，不能只看日期"""
        a = self.store.bind(date(2026, 10, 28), "小额经营贷", "二级分行")
        b = self.store.bind(date(2024, 6, 30), "小额经营贷", "二级分行")
        self.assertNotEqual(a.version_of("R-CR-013"), b.version_of("R-CR-013"))

    # ---------- 数据质量 ----------
    def test_重复rule_id加version被拒(self):
        rules = seed_rules()
        rules.append(rules[0])  # 同一 identity 再来一条
        with self.assertRaises(ValueError):
            RuleStore(rules)

    def test_同一rule_id不同version允许共存(self):
        ids = {r.rule_id for r in seed_rules()}
        self.assertIn("R-CR-013", ids)

    def test_种子规则集本身合法(self):
        RuleStore(seed_rules())  # 不抛异常即通过


if __name__ == "__main__":
    unittest.main()
