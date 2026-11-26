"""双路抽取与合并（PRD 6.2 FR-FACT-02）。"""
from __future__ import annotations

import math
import unittest

from finguard.facts.extract import RouteResult, distinct_candidates, merge_dual
from finguard.facts.models import Confidence, SourceChannel


def route_a(values, rejected=(), channel=SourceChannel.OCR, route="vote"):
    return RouteResult(route=route, channel=channel, values=dict(values),
                       rejected=tuple(rejected))


def route_b(values, rejected=(), channel=SourceChannel.OCR, route="rule_check"):
    return RouteResult(route=route, channel=channel, values=dict(values),
                       rejected=tuple(rejected))


class TestMergeDual(unittest.TestCase):
    def test_两路一致判高置信(self):
        merged = merge_dual(route_a({"x": 1}), route_b({"x": 1}))
        f = merged["x"]
        self.assertIs(f.confidence, Confidence.HIGH)
        self.assertEqual(f.value, 1)
        self.assertEqual(f.candidates, ())

    def test_两路不一致判低置信并列两候选(self):
        merged = merge_dual(route_a({"x": 1}), route_b({"x": 2}))
        f = merged["x"]
        self.assertIs(f.confidence, Confidence.LOW)
        self.assertEqual(f.candidates, (1, 2))

    def test_仅一路抽到判低置信(self):
        merged = merge_dual(route_a({"x": 1}), route_b({}))
        f = merged["x"]
        self.assertIs(f.confidence, Confidence.LOW)
        self.assertEqual(f.candidates, (1,))

    def test_两路都没抽到字段不存在(self):
        merged = merge_dual(route_a({}), route_b({}))
        self.assertNotIn("x", merged)

    def test_规则校验路拒绝等同没抽到(self):
        merged = merge_dual(route_a({"x": 1}), route_b({"x": 1}, rejected=("x",)))
        self.assertIs(merged["x"].confidence, Confidence.LOW)

    def test_布尔与数字不算一致(self):
        """0 == False 在 Python 里为真，但两者语义不同，必须判为不一致。"""
        merged = merge_dual(route_a({"x": 0}), route_b({"x": False}))
        self.assertIs(merged["x"].confidence, Confidence.LOW)

    def test_NaN两路都给判为低置信(self):
        """NaN == NaN 为 False；两路都给 NaN 仍需人工裁决，不放行。"""
        merged = merge_dual(route_a({"x": float("nan")}), route_b({"x": float("nan")}))
        self.assertIs(merged["x"].confidence, Confidence.LOW)

    def test_来源与路径被记录(self):
        merged = merge_dual(
            route_a({"x": 1}, channel=SourceChannel.OCR),
            route_b({"x": 1}, channel=SourceChannel.API),
        )
        self.assertEqual(set(merged["x"].channels), {SourceChannel.OCR, SourceChannel.API})
        self.assertEqual(set(merged["x"].routes), {"vote", "rule_check"})

    def test_不修改入参(self):
        a = route_a({"x": 1})
        b = route_b({"x": 1})
        snapshot = (dict(a.values), dict(b.values))
        merge_dual(a, b)
        self.assertEqual((a.values, b.values), snapshot)

    def test_候选值去重保序(self):
        self.assertEqual(distinct_candidates([1, 2, 1, 3]), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()


class TestStrictCandidateEquality(unittest.TestCase):
    """评审回归：布尔/数字等价不得塌缩候选值（FR-FACT-06 并列两取值）。"""

    def test_布尔与数字不一致候选保留两值(self):
        merged = merge_dual(route_a({"x": False}), route_b({"x": 0}))
        self.assertIs(merged["x"].confidence, Confidence.LOW)
        self.assertEqual(merged["x"].candidates, (False, 0))

    def test_真与1候选保留两值(self):
        merged = merge_dual(route_a({"x": True}), route_b({"x": 1}))
        self.assertEqual(merged["x"].candidates, (True, 1))

    def test_候选去重布尔数字不塌缩(self):
        self.assertEqual(distinct_candidates([False, 0, True, 1]), (False, 0, True, 1))
