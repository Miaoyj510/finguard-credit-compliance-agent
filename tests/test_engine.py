"""确定性判定引擎（PRD 6.1 FR-RAG-01、6.3 FR-AUDIT-02/03、8.8 F-02）。"""
from __future__ import annotations

import unittest
from datetime import date

from finguard.domain.case import TripleKey
from finguard.domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from finguard.domain.rule import Rule
from finguard.rules.engine import RuleEngine
from finguard.rules.registry import RuleStore
from finguard.rules.seeds import seed_rules


def make_rule(**overrides) -> Rule:
    base = dict(
        rule_id="R-TEST-001",
        version="1.0.0",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        product_scope=("*",),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('申报营收与流水不符')",
        required_facts=("monthly_revenue", "declared_revenue"),
        source_ref="《测试规程》第一条（合成条款）",
    )
    base.update(overrides)
    return Rule(**base)


class TestVerdictStates(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RuleEngine()
        self.rule = make_rule()

    def test_条件成立返回命中(self):
        r = self.engine.evaluate(self.rule, {"monthly_revenue": 820000, "declared_revenue": 1000000})
        self.assertIs(r.verdict, Verdict.HIT)
        self.assertIs(r.coverage, CoverageFlag.JUDGED)
        self.assertEqual(r.hit_label, "申报营收与流水不符")

    def test_条件不成立返回未命中(self):
        r = self.engine.evaluate(self.rule, {"monthly_revenue": 1200000, "declared_revenue": 1000000})
        self.assertIs(r.verdict, Verdict.MISS)
        self.assertIs(r.coverage, CoverageFlag.JUDGED)
        self.assertIsNone(r.hit_label)

    def test_规则不适用返回不适用(self):
        """产品不在适用范围 -> 不适用（不是未命中）。"""
        rule = make_rule(product_scope=("大额授信",))
        ctx = TripleKey(date(2026, 10, 28), "小额经营贷", "二级分行")
        r = self.engine.evaluate(rule, {"monthly_revenue": 1, "declared_revenue": 2}, ctx)
        self.assertIs(r.verdict, Verdict.NOT_APPLICABLE)
        self.assertIs(r.coverage, CoverageFlag.NOT_APPLICABLE)

    def test_不传context表示调用方已绑定(self):
        """context=None 的语义必须被测试钉住：不做范围复核。"""
        rule = make_rule(product_scope=("大额授信",))
        r = self.engine.evaluate(rule, {"monthly_revenue": 1, "declared_revenue": 2})
        self.assertIs(r.coverage, CoverageFlag.JUDGED)

    def test_生效期不匹配返回不适用(self):
        ctx = TripleKey(date(2024, 1, 1), "小额经营贷", "二级分行")
        r = self.engine.evaluate(self.rule, {"monthly_revenue": 1, "declared_revenue": 2}, ctx)
        self.assertIs(r.coverage, CoverageFlag.NOT_APPLICABLE)

    def test_覆盖度与三态的不变式(self):
        """coverage == 已判定 当且仅当 verdict 属于 {命中, 未命中}。"""
        cases = [
            (self.rule, {"monthly_revenue": 1, "declared_revenue": 2}),
            (self.rule, {"monthly_revenue": 3, "declared_revenue": 2}),
            (self.rule, {"monthly_revenue": 1}),
            (make_rule(dsl="IF fact.a < 1 THEN hit('x')"), {"a": "str"}),
            (make_rule(dsl="IF fact.a < THEN hit('x')"), {"a": 1}),
            (make_rule(product_scope=("其他",)), {"a": 1}),
        ]
        for rule, facts in cases:
            with self.subTest(rule=rule.rule_id, dsl=rule.dsl):
                r = self.engine.evaluate(rule, facts)
                if r.coverage is CoverageFlag.JUDGED:
                    self.assertIn(r.verdict, (Verdict.HIT, Verdict.MISS))
                else:
                    self.assertIs(r.verdict, Verdict.NOT_APPLICABLE)


class TestUnresolvedDegradation(unittest.TestCase):
    """坏规则不许带崩整批审查——一律降级为「未判定」并带原因码。"""

    def setUp(self) -> None:
        self.engine = RuleEngine()

    def test_事实缺失转未判定(self):
        rule = make_rule()
        r = self.engine.evaluate(rule, {"monthly_revenue": 820000})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.MISSING_FACT)
        self.assertIn("declared_revenue", r.reason)

    def test_事实为None视为缺失(self):
        rule = make_rule()
        r = self.engine.evaluate(rule, {"monthly_revenue": 1, "declared_revenue": None})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.MISSING_FACT)

    def test_表达式无法解析降级为未判定(self):
        rule = make_rule(dsl="IF fact.a < THEN hit('x')")
        r = self.engine.evaluate(rule, {"a": 1})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.DSL_PARSE_ERROR)

    def test_类型不可比降级为未判定(self):
        """字符串与数字比较 -> 不可比 -> 未判定，而不是判成 False。"""
        rule = make_rule(dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('x')")
        r = self.engine.evaluate(rule, {"monthly_revenue": "十万", "declared_revenue": 100})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.TYPE_MISMATCH)

    def test_布尔值不被当作数字(self):
        rule = make_rule(dsl="IF fact.a < 1 THEN hit('x')")
        r = self.engine.evaluate(rule, {"a": True})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.TYPE_MISMATCH)

    def test_引擎不抛异常(self):
        for rule, facts in (
            (make_rule(dsl="这不是 DSL"), {"a": 1}),
            (make_rule(dsl="IF fact.a < THEN hit('x')"), {}),
            (make_rule(), {"a": object()}),
        ):
            with self.subTest(dsl=rule.dsl):
                r = self.engine.evaluate(rule, facts)   # 不应抛出
                self.assertIsNotNone(r)


    def test_引擎内部异常标ENGINE_ERROR(self):
        """兜底分支不得误标成 DSL_PARSE_ERROR——审计口径要能区分「规则写错」与「引擎坏了」。"""
        import unittest.mock as mock

        engine = RuleEngine()
        rule = make_rule()
        facts = {"monthly_revenue": 1, "declared_revenue": 2}
        with mock.patch.object(RuleEngine, "_parse", side_effect=RuntimeError("boom")):
            r = engine.evaluate(rule, facts)
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.ENGINE_ERROR)
        self.assertNotEqual(r.reason_code, UnresolvedReasonCode.DSL_PARSE_ERROR)


class TestSeverityFromRule(unittest.TestCase):
    """严重度只能来自规则库，模型不得自拟（FR-AUDIT-03）。"""

    def test_严重度取自规则(self):
        engine = RuleEngine()
        for sev in Severity:
            with self.subTest(severity=sev):
                rule = make_rule(severity=sev)
                r = engine.evaluate(rule, {"monthly_revenue": 1, "declared_revenue": 2})
                self.assertIs(r.severity, sev)

    def test_判定接口不接受严重度入参(self):
        import inspect

        sig = inspect.signature(RuleEngine.evaluate)
        self.assertNotIn("severity", sig.parameters)


class TestReproducibility(unittest.TestCase):
    """判定可复算：同输入必得同输出（PRD 指标 Q-04 的一致性基础）。"""

    def setUp(self) -> None:
        self.engine = RuleEngine()
        self.rule = make_rule()

    def test_两次判定结果完全相等(self):
        facts_a = {"monthly_revenue": 820000, "declared_revenue": 1000000}
        facts_b = {"monthly_revenue": 820000, "declared_revenue": 1000000}
        self.assertEqual(self.engine.evaluate(self.rule, facts_a),
                         self.engine.evaluate(self.rule, facts_b))

    def test_结果不可变且可哈希(self):
        r = self.engine.evaluate(self.rule, {"monthly_revenue": 1, "declared_revenue": 2})
        with self.assertRaises(AttributeError):
            r.verdict = Verdict.MISS     # type: ignore[misc]
        self.assertEqual(len({r}), 1)

    def test_引擎不修改传入的事实(self):
        facts = {"monthly_revenue": 820000, "declared_revenue": 1000000}
        snapshot = dict(facts)
        self.engine.evaluate(self.rule, facts)
        self.assertEqual(facts, snapshot)

    def test_不同引擎实例结果一致(self):
        facts = {"monthly_revenue": 820000, "declared_revenue": 1000000}
        self.assertEqual(
            RuleEngine().evaluate(self.rule, facts),
            RuleEngine().evaluate(self.rule, facts),
        )

    def test_表达式缓存不改变结果(self):
        engine = RuleEngine()
        facts = {"monthly_revenue": 1, "declared_revenue": 2}
        first = engine.evaluate(self.rule, facts)
        second = engine.evaluate(self.rule, facts)   # 第二次走缓存
        self.assertEqual(first, second)


class TestEvaluateAll(unittest.TestCase):
    def test_批量判定顺序与入参一致(self):
        engine = RuleEngine()
        rules = list(seed_rules())
        facts = {"monthly_revenue": 820000, "declared_revenue": 1000000,
                 "tenor_months": 24, "loan_amount": 1000000, "industry": "零售", "branch_count": 1}
        results = engine.evaluate_all(rules, facts)
        self.assertEqual(len(results), len(rules))
        for rule, r in zip(rules, results):
            self.assertEqual(r.rule_id, rule.rule_id)

    def test_批量判定中坏规则不影响其他规则(self):
        engine = RuleEngine()
        good = make_rule(rule_id="R-TEST-001")
        bad = make_rule(rule_id="R-TEST-002", dsl="IF fact.a < THEN hit('x')")
        facts = {"monthly_revenue": 1, "declared_revenue": 2}
        results = engine.evaluate_all([good, bad, good], facts)
        self.assertIs(results[0].coverage, CoverageFlag.JUDGED)
        self.assertIs(results[1].coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(results[2].coverage, CoverageFlag.JUDGED)


class TestStoreGet(unittest.TestCase):
    def test_按版本精确取规则(self):
        store = RuleStore(seed_rules())
        rule = store.get("R-CR-013", "3.0.0")
        self.assertEqual(rule.version, "3.0.0")

    def test_取不存在的版本直接报错(self):
        store = RuleStore(seed_rules())
        with self.assertRaises(KeyError):
            store.get("R-CR-013", "9.9.9")


class TestEngineContract(unittest.TestCase):
    """评审补强：降级契约的边界——数据问题降级，调用方 bug 该抛就抛。"""

    def setUp(self) -> None:
        self.engine = RuleEngine()
        self.rule = make_rule(dsl="IF fact.a < 100 THEN hit('x')")

    def test_非Rule入参抛TypeError(self):
        """「永不抛异常」针对坏数据，不针对违反类型契约的调用方 bug。"""
        with self.assertRaises(TypeError):
            self.engine.evaluate(None, {})

    def test_facts为None按缺失降级(self):
        """None 语义上等于「没有事实」，应走 MISSING_FACT，而不是兜底误标 DSL_PARSE_ERROR。"""
        r = self.engine.evaluate(self.rule, None)
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.MISSING_FACT)

    def test_facts非Mapping抛TypeError(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate(self.rule, 123)

    def test_NaN小于比较降级为未判定(self):
        """NaN 与任何数比较都得 False——不能把无效数据判成确定的「未命中」。"""
        r = self.engine.evaluate(self.rule, {"a": float("nan")})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.TYPE_MISMATCH)

    def test_NaN不等于比较不得判命中(self):
        """NaN != 100 在 Python 里为 True——更危险，会把无效数据判成确定命中。"""
        rule = make_rule(dsl="IF fact.a != 100 THEN hit('x')")
        r = self.engine.evaluate(rule, {"a": float("nan")})
        self.assertIs(r.coverage, CoverageFlag.UNRESOLVED)
        self.assertIs(r.reason_code, UnresolvedReasonCode.TYPE_MISMATCH)

    def test_无穷大视为合法数值(self):
        """inf 是有意义的无界值，与 NaN 不同，允许参与判定（钉住设计取舍）。"""
        r = self.engine.evaluate(self.rule, {"a": float("inf")})
        self.assertIs(r.verdict, Verdict.MISS)   # inf < 100 为假
        self.assertIs(r.coverage, CoverageFlag.JUDGED)

    def test_字符串按字典序比较(self):
        """同为字符串时允许比较（ISO 日期串等场景），钉住设计取舍。"""
        rule = make_rule(dsl="IF fact.a < fact.b THEN hit('x')")
        r = self.engine.evaluate(rule, {"a": "abc", "b": "abd"})
        self.assertIs(r.verdict, Verdict.HIT)
        self.assertIs(r.coverage, CoverageFlag.JUDGED)


if __name__ == "__main__":
    unittest.main()
