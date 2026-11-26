"""审查意见模型与引用闸门（PRD 6.3 FR-AUDIT-01 ~ 07）。"""
from __future__ import annotations

import unittest

from finguard.agents.auditor import CitationGate
from finguard.domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from finguard.domain.opinion import (
    BlockedOpinion,
    CoverageSummary,
    OpinionDraft,
    OpinionOrigin,
    ReviewOpinion,
    SeverityCorrection,
    UnresolvedEntry,
)
from finguard.rules.engine import Evaluation


def evaluation(rule_id="R-CR-013", version="3.2.0", fact_refs=("monthly_revenue", "declared_revenue")):
    return Evaluation(
        rule_id=rule_id, rule_version=version,
        verdict=Verdict.HIT, coverage=CoverageFlag.JUDGED,
        reason="命中", reason_code=None,
        fact_refs=tuple(fact_refs), values=(),
        severity=Severity.MAJOR, hit_label="申报营收与流水不符",
    )


class TestCitationGate(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = CitationGate()
        self.ev = evaluation()

    def _draft(self, **kw) -> OpinionDraft:
        base = dict(
            rule_id="R-CR-013", rule_version="3.2.0", severity=Severity.MAJOR,
            fact_refs=("monthly_revenue", "declared_revenue"),
            rationale="触发依据：《信贷业务审查操作规程》第四条",
        )
        base.update(kw)
        return OpinionDraft(**base)

    def test_三件套齐全通过(self):
        self.assertEqual(self.gate.check(self._draft(), self.ev), ())

    def test_缺rule_id被拦(self):
        self.assertIn("rule_id", self.gate.check(self._draft(rule_id=""), self.ev))

    def test_缺rule_version被拦(self):
        self.assertIn("rule_version", self.gate.check(self._draft(rule_version=""), self.ev))

    def test_缺fact_refs被拦(self):
        self.assertIn("fact_refs", self.gate.check(self._draft(fact_refs=()), self.ev))

    def test_引用了别的规则被拦(self):
        self.assertIn("rule_id", self.gate.check(self._draft(rule_id="R-CR-999"), self.ev))

    def test_引用了别的版本被拦(self):
        self.assertIn("rule_version", self.gate.check(self._draft(rule_version="9.9.9"), self.ev))

    def test_事实引用没覆盖判定用到的字段被拦(self):
        self.assertIn("fact_refs", self.gate.check(self._draft(fact_refs=("monthly_revenue",)), self.ev))

    def test_多余的事实引用不算缺(self):
        draft = self._draft(fact_refs=("monthly_revenue", "declared_revenue", "extra"))
        self.assertEqual(self.gate.check(draft, self.ev), ())

    def test_判定未用任何事实时空引用不算缺(self):
        ev = evaluation(fact_refs=())
        draft = self._draft(fact_refs=())
        self.assertEqual(self.gate.check(draft, ev), ())


class TestCoverageSummary(unittest.TestCase):
    def test_三类计数自洽(self):
        c = CoverageSummary(judged=3, not_applicable=1, unresolved=1, total=5)
        self.assertTrue(c.is_consistent)

    def test_计数不自洽能被发现(self):
        c = CoverageSummary(judged=3, not_applicable=1, unresolved=1, total=6)
        self.assertFalse(c.is_consistent)

    def test_可序列化(self):
        c = CoverageSummary(judged=1, total=1, unresolved_entries=(
            UnresolvedEntry("R-X", "1.0.0", UnresolvedReasonCode.MISSING_FACT, "缺字段"),))
        d = c.to_dict()
        self.assertTrue(d["is_consistent"])
        self.assertEqual(d["unresolved_entries"][0]["reason_code"], "MISSING_FACT")


class TestOpinionModels(unittest.TestCase):
    def test_意见不可变(self):
        o = ReviewOpinion(
            opinion_id="OP-1", rule_id="R-CR-013", rule_version="3.2.0",
            severity=Severity.MAJOR, fact_refs=("a",), rationale="r",
            coverage=CoverageFlag.JUDGED, origin=OpinionOrigin.RULE_ENGINE,
        )
        with self.assertRaises(Exception):
            o.severity = Severity.INFO

    def test_拦截记录可序列化(self):
        b = BlockedOpinion("R-CR-013", "3.2.0", "引用三件套不完整", missing=("fact_refs",))
        self.assertEqual(b.to_dict()["missing"], ["fact_refs"])

    def test_严重度替换记录可序列化(self):
        c = SeverityCorrection("R-CR-013", "3.2.0", Severity.INFO, Severity.MAJOR)
        d = c.to_dict()
        self.assertEqual(d["proposed"], "提示")
        self.assertEqual(d["applied"], "重要")


if __name__ == "__main__":
    unittest.main()
