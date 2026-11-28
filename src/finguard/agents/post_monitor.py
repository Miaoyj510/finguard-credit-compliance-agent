"""贷后监测 Agent：常驻循环、扫描与预警生成（PRD 6.4 FR-POST-01 ~ 04）。

**常驻循环与主流程解耦**（FR-POST-01）不是靠"我们不阻塞"，而是靠**结构**：
本 Agent **不持有**主流程的 ``CaseFile``，只接收不可变的 ``PostCaseView``。
没有引用，就无从阻塞；主流程推进也不依赖本 Agent 是否在跑。

三个容易写错的地方，这里钉死：

1. **规则版本按放款日取，命中时点按扫描日记**（FR-POST-02 / FR-POST-03）。
   视图里的 ``rule_bindings`` 是放款日按三键取到的版本，贷前贷后同一
   ``rule_id`` 因此取到**同一版本**；而 ``hit_at`` 是扫描日，单独记字段。
2. **提前量以放款日为基准**（FR-POST-04），不是以系统上线日为基准。
3. **证据快照不可被后续覆盖**（FR-POST-03）：快照在生成时冻结，
   之后事实层再怎么变，也改不动"命中当时看到了什么"。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..domain.enums import Severity, Verdict
from ..domain.post import (
    Alert,
    AlertLevel,
    PostCaseView,
    freeze_snapshot,
)
from ..rules.engine import Evaluation, RuleEngine

__all__ = ["PostMonitorAgent", "SEVERITY_TO_LEVEL"]


#: 规则严重度 -> 预警等级（相对粗的映射，只用于分档展示）
SEVERITY_TO_LEVEL: Mapping[Severity, AlertLevel] = {
    Severity.CRITICAL: AlertLevel.HIGH,
    Severity.MAJOR: AlertLevel.MEDIUM,
    Severity.ATTENTION: AlertLevel.MEDIUM,
    Severity.INFO: AlertLevel.LOW,
}


class PostMonitorAgent:
    """贷后监测 Agent（常驻循环）。"""

    def __init__(
        self,
        rules: Mapping[str, Any],
        *,
        engine: Optional[RuleEngine] = None,
        clock: Optional[Any] = None,
    ) -> None:
        # rules: {"rule_id@version": Rule}——与贷前**同一个规则库**（FR-POST-02）
        self._rules = dict(rules)
        self._engine = engine or RuleEngine()
        self._clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._views: Dict[str, PostCaseView] = {}
        self._alerts: List[Alert] = []
        self._alert_keys: Dict[Tuple[str, str], str] = {}   # (case_id, rule_id) -> alert_id
        self._running = False

    # ---------------- 启停（FR-POST-01：可独立启停） ----------------
    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # ---------------- 监测名单 ----------------
    def register(self, view: PostCaseView) -> None:
        """把放款后案件纳入监测名单。

        传入的是**只读视图**，不是主流程的案件对象——这是解耦的结构保证。
        """
        self._views[view.case_id] = view

    def unregister(self, case_id: str) -> None:
        self._views.pop(case_id, None)

    @property
    def monitored(self) -> Tuple[str, ...]:
        return tuple(sorted(self._views))

    @property
    def alerts(self) -> Tuple[Alert, ...]:
        return tuple(self._alerts)

    # ---------------- 扫描 ----------------
    def scan_once(self, as_of: Optional[str] = None) -> Tuple[Alert, ...]:
        """扫描一轮，产出新预警。

        **幂等的正确口径**（评审 R-P1）：去重键是
        ``案件 + 规则 + 命中事实取值指纹``——防的是「同一事实被反复扫描
        刷出重复预警」（那会让采纳率分母失真）；**不是**「同一规则只报一次」。
        常驻循环的价值在持续监测：下一期逾期天数变了就是新的事实、新的预警。
        原实现按 (case_id, rule_id) 去重，等于每条规则生命周期内只报一次，
        贷后监测在第一次预警后就名存实亡，且漏报方向无声无息。
        """
        if not self._running:
            return ()
        moment = as_of or self._clock()
        produced: List[Alert] = []
        for case_id in sorted(self._views):
            view = self._views[case_id]
            for rule_id, version in view.rule_bindings:
                key = "%s@%s" % (rule_id, version)
                rule = self._rules.get(key)
                if rule is None:
                    continue
                evaluation = self._engine.evaluate(rule, view.facts)
                if evaluation.verdict is not Verdict.HIT:
                    continue
                fact_key = self._fact_fingerprint(view, evaluation)
                if (case_id, rule_id, fact_key) in self._alert_keys:
                    continue                      # 幂等：同事实已有预警
                produced.append(
                    (self._make_alert(view, rule, evaluation, moment), fact_key)
                )
        out: List[Alert] = []
        for alert, fact_key in produced:
            self._alerts.append(alert)
            self._alert_keys[(alert.case_id, alert.rule_id, fact_key)] = alert.alert_id
            out.append(alert)
        return tuple(out)

    @staticmethod
    def _fact_fingerprint(view: PostCaseView, evaluation: Evaluation) -> str:
        """命中事实取值的规范化指纹（同事实同指纹，新事实新指纹）。"""
        pairs = sorted(
            (name, view.facts.get(name)) for name in evaluation.fact_refs
        )
        return json.dumps(pairs, sort_keys=True, ensure_ascii=False, default=str)

    # ---------------- 预警构造 ----------------
    def _make_alert(
        self,
        view: PostCaseView,
        rule: Any,
        evaluation: Evaluation,
        as_of: str,
    ) -> Alert:
        baseline = view.baseline_date
        lead = self._lead_time(baseline, as_of)
        evidence = {
            "rule": {
                "rule_id": rule.rule_id,
                "version": rule.version,
                "source_ref": rule.source_ref,
            },
            "facts": {
                name: dict(
                    dict(view.field_meta.get(name, {})), value=view.facts.get(name)
                )
                for name in evaluation.fact_refs
            },
            "evaluation": {
                "verdict": evaluation.verdict.value,
                "hit_label": evaluation.hit_label,
                "values": {k: v for k, v in evaluation.values},
            },
            "scanned_at": as_of,
            "baseline_date": baseline,
        }
        # 同一案件同一规则可能因事实变化多次预警，编号带序号保证唯一
        seq = sum(
            1 for a in self._alerts
            if a.case_id == view.case_id and a.rule_id == rule.rule_id
        ) + 1
        return Alert(
            alert_id="AL-%s-%s-%03d" % (view.case_id, rule.rule_id, seq),
            case_id=view.case_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            hit_at=as_of,
            baseline_date=baseline,
            lead_time_days=lead,
            level=SEVERITY_TO_LEVEL.get(evaluation.severity, AlertLevel.LOW),
            evidence=freeze_snapshot(evidence),
        )

    @staticmethod
    def _lead_time(baseline_date: str, as_of: str) -> int:
        """提前量 = 命中时点 - 基准日（放款日 / 业务发生日）。

        基准**永远取自案件**，与系统上线日无关（FR-POST-04）。
        """
        base = datetime.strptime(baseline_date[:10], "%Y-%m-%d").date()
        hit = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
        return (hit - base).days