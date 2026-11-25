"""DSL 解析（PRD 6.1 FR-RAG-01：判定走确定性引擎，规则内容不被当作代码执行）。"""
from __future__ import annotations

import unittest

from finguard.rules.dsl import (
    Comparison,
    DslParseError,
    FieldRef,
    Literal,
    parse_expression,
)


class TestParseValid(unittest.TestCase):
    def test_字段比较可解析(self):
        e = parse_expression(
            "IF fact.monthly_revenue < fact.declared_revenue THEN hit('申报营收与流水不符')"
        )
        self.assertEqual(e.label, "申报营收与流水不符")
        self.assertIsInstance(e.comparison.lhs, FieldRef)
        self.assertEqual(e.comparison.lhs.name, "monthly_revenue")
        self.assertIsInstance(e.comparison.rhs, FieldRef)
        self.assertEqual(e.comparison.op, "<")

    def test_字段与数字常量比较(self):
        e = parse_expression("IF fact.tenor_months > 36 THEN hit('期限超出产品上限')")
        self.assertIsInstance(e.comparison.rhs, Literal)
        self.assertEqual(e.comparison.rhs.value, 36)

    def test_字段与字符串常量比较(self):
        e = parse_expression("IF fact.industry == '房地产' THEN hit('行业集中度需复核')")
        self.assertEqual(e.comparison.rhs.value, "房地产")

    def test_六种比较运算符都支持(self):
        for op in ("<", "<=", ">", ">=", "==", "!="):
            with self.subTest(op=op):
                e = parse_expression("IF fact.a %s 1 THEN hit('x')" % op)
                self.assertEqual(e.comparison.op, op)

    def test_浮点与负数字面量(self):
        self.assertEqual(parse_expression("IF fact.a < 3.5 THEN hit('x')").comparison.rhs.value, 3.5)
        self.assertEqual(parse_expression("IF fact.a == -12 THEN hit('x')").comparison.rhs.value, -12)

    def test_双引号字符串(self):
        self.assertEqual(parse_expression('IF fact.a == "x" THEN hit(\'y\')').comparison.rhs.value, "x")

    def test_字段名列表可提取(self):
        e = parse_expression("IF fact.a < fact.b THEN hit('x')")
        self.assertEqual(e.field_names(), ("a", "b"))

    def test_解析结果不可变且可哈希(self):
        e = parse_expression("IF fact.a < 1 THEN hit('x')")
        with self.assertRaises(Exception):
            e.label = "y"  # type: ignore[misc]
        self.assertEqual(len({e, e}), 1)

    def test_同一表达式重复解析结果一致(self):
        t = "IF fact.a < fact.b THEN hit('x')"
        self.assertEqual(parse_expression(t), parse_expression(t))


class TestParseInvalid(unittest.TestCase):
    def _reject(self, text: str) -> DslParseError:
        with self.assertRaises(DslParseError):
            parse_expression(text)
        try:
            parse_expression(text)
        except DslParseError as exc:
            return exc
        raise AssertionError("应当抛出 DslParseError")

    def test_缺少操作数(self):
        self._reject("IF fact.a < THEN hit('x')")

    def test_IF_必须大写(self):
        self._reject("if fact.a < 1 THEN hit('x')")

    def test_THEN_必须大写(self):
        self._reject("IF fact.a < 1 then hit('x')")

    def test_hit_必须小写(self):
        self._reject("IF fact.a < 1 THEN HIT('x')")

    def test_label_必须是字符串(self):
        self._reject("IF fact.a < 1 THEN hit(123)")

    def test_尾部有多余内容(self):
        self._reject("IF fact.a < 1 THEN hit('x') extra")

    def test_字段名缺失或非法(self):
        self._reject("IF fact. < 1 THEN hit('x')")
        self._reject("IF fact.1a < 1 THEN hit('x')")
        self._reject("IF fact.IF < 1 THEN hit('x')")

    def test_括号不配对(self):
        self._reject("IF fact.a < 1 THEN hit('x")

    def test_空表达式(self):
        self._reject("")
        self._reject("   ")

    def test_错误带位置信息(self):
        exc = self._reject("IF fact.a < 1 THEN hit('x")
        self.assertIsNotNone(exc.position)
        self.assertIn("字符", str(exc))


class TestNoCodeExecution(unittest.TestCase):
    """规则 DSL 是外部内容——绝不能被当成代码执行（安全硬要求）。"""

    def test_拒绝函数调用与导入(self):
        for text in (
            "import os",
            "IF __import__('os').system('echo pwned') < 1 THEN hit('x')",
            "IF eval('1+1') < 2 THEN hit('x')",
            "IF fact.a.__class__ < 1 THEN hit('x')",
        ):
            with self.subTest(text=text):
                with self.assertRaises(DslParseError):
                    parse_expression(text)

    def test_解析器源码里不出现eval或exec(self):
        import inspect

        from finguard.rules import dsl

        src = inspect.getsource(dsl)
        self.assertNotIn("eval(", src)
        self.assertNotIn("exec(", src)


if __name__ == "__main__":
    unittest.main()
