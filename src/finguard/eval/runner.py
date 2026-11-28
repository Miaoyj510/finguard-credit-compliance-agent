# -*- coding: utf-8 -*-
"""评测跑批与报告（PRD 12.3 / 12.4 · 切片 8.1）。

**这一层真跑管线**：不是拿金标跟金标比，也不是拿实现跟实现比。
每一件金标案件都走一遍**真实链路**：

    金标 facts → 事实集（已裁决口径）
             → RuleStore.bind（三键取版本，切片 1）
             → RuleEngine.evaluate（DSL 判定，切片 2）
             → Auditor.build（意见生成 + 引用闸门 + 禁止行为，切片 4）
             → 与金标逐项比对

事实层另跑一遍（切片 3）：按 ``field_samples`` 重建两路抽取，
过 FactLayer（双路合并 → 交叉核对 → 冲突入队 → 判定链放行）。

报告落 JSON，**随评测集版本号存档**（PRD 12.3 评测纪律第 1 条），
保证同版本金标 + 同版本代码可复算出同一份结果。
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..agents.auditor import Auditor
from ..facts.layer import FactLayer
from ..knowledge.guard import screen_text
from ..rules.engine import RuleEngine
from ..rules.registry import RuleStore
from ..rules.seeds import seed_rules
from .gates import GateResult, evaluate_gates, overall_verdict
from .loader import (
    GoldCase,
    GoldSet,
    default_gold_path,
    default_injection_path,
    decision_factset,
    derive_eval_sets,
    fact_routes,
    load_gold,
)
from .metrics import CaseRun, FactReplay, InjectionCase, MetricSet, compute_metric_set

__all__ = [
    "EvalReport",
    "replay_case",
    "replay_fact_layer",
    "load_injection_cases",
    "run_evaluation",
    "write_report",
    "report_dir",
]


# ---------------------------------------------------------------- 跑批
def replay_case(
    case: GoldCase,
    store: RuleStore,
    engine: RuleEngine,
    auditor: Auditor,
) -> CaseRun:
    """把一件金标案件跑完判定 + 意见生成链路。"""
    errors: List[str] = []
    binding = store.bind(
        case.triple_key.business_date,
        case.triple_key.product_type,
        case.triple_key.org_level,
    )
    factset = decision_factset(case)
    ready = factset.decision_ready()

    evaluations = []
    rules_map: Dict[str, Any] = {}
    for bound in binding.bindings:
        try:
            rule = store.get(bound.rule_id, bound.version)
        except KeyError as exc:
            errors.append("规则缺失：%s" % exc)
            continue
        rules_map["%s@%s" % (bound.rule_id, bound.version)] = rule
        evaluations.append(
            engine.evaluate(rule, ready, context=case.triple_key))

    result = auditor.build(
        evaluations,
        factset,
        rules_map,
        case_id=case.case_id,
        bound_rule_total=len(binding.bindings),
        unresolved_bindings=binding.unresolved,
    )

    return CaseRun(
        case_id=case.case_id,
        bucket=case.bucket,
        triple_key=case.triple_key,
        bindings=tuple(
            (b.rule_id, b.version, None) for b in binding.bindings
        ) + tuple(
            (u.rule_id, None, u.reason_code.value) for u in binding.unresolved
        ),
        evaluations=tuple(evaluations),
        opinions=result.opinions,
        blocked=result.blocked,
        coverage=result.coverage,
        factset=factset,
        errors=tuple(errors),
    )


def replay_fact_layer(case: GoldCase) -> FactReplay:
    """按字段样本重建两路抽取，跑一遍事实层。"""
    errors: List[str] = []
    route_a, route_b = fact_routes(case)
    layer = FactLayer()
    try:
        factset, queue = layer.build(route_a, route_b)
    except Exception as exc:                     # 事实层硬失败必须留痕，不能吞
        errors.append("事实层异常：%s: %s" % (exc.__class__.__name__, exc))
        return FactReplay(case_id=case.case_id, bucket=case.bucket, errors=tuple(errors))
    ready = factset.decision_ready()
    return FactReplay(
        case_id=case.case_id,
        bucket=case.bucket,
        merged=dict(factset.fields),
        ready=tuple(sorted(ready.keys())),
        blocked=tuple(factset.blocked_fields()),
        checks=tuple(factset.checks),
        conflicts=tuple(queue.all()),
        errors=tuple(errors),
    )


def load_injection_cases(path: Optional[str] = None) -> Tuple[InjectionCase, ...]:
    """读注入样本集并逐条跑入厂筛查（切片 6.5 的 screen_text）。

    口径：入厂筛查用的**就是运行时那套词表**（结论词表 + FB-05 指令词表），
    因此 SEC-05 测的就是运行时真正会拦的那道闸。
    """
    target = path or default_injection_path()
    if not os.path.exists(target):
        return ()
    with io.open(target, encoding="utf-8") as fh:
        raw = json.load(fh)
    out: List[InjectionCase] = []
    for item in raw.get("samples", ()):
        screen = screen_text(item.get("text", ""), item.get("sample_id", ""))
        out.append(
            InjectionCase(
                sample_id=item.get("sample_id", ""),
                text=item.get("text", ""),
                expect_isolated=bool(item.get("expect_isolated", False)),
                kind=item.get("kind", ""),
                actual_isolated=screen.isolated,
                reasons=tuple(screen.reasons),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------- 报告
@dataclass(frozen=True)
class EvalReport:
    """一次评测的完整结果。"""

    generated_at: str
    gold: Mapping[str, Any]
    metrics: MetricSet
    gates: Tuple[GateResult, ...]
    verdict: Mapping[str, Any]
    cases: Tuple[CaseRun, ...] = ()
    fact_replays: Tuple[FactReplay, ...] = ()
    injection: Tuple[InjectionCase, ...] = ()
    observations: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "gold": dict(self.gold),
            "metrics": self.metrics.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "verdict": dict(self.verdict),
            "cases": [c.to_dict() for c in self.cases],
            "fact_replays": [f.to_dict() for f in self.fact_replays],
            "injection": [i.to_dict() for i in self.injection],
            "observations": list(self.observations),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


def report_dir(root: Optional[str] = None) -> str:
    base = root or _repo_root()
    return os.path.join(base, "reports", "eval")


def write_report(report: EvalReport, directory: Optional[str] = None) -> str:
    """把报告写成 JSON 存档（随评测集版本号）。"""
    target_dir = directory or report_dir()
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    stamp = report.generated_at.replace(":", "").replace("-", "")
    name = "eval_%s_%s.json" % (report.gold.get("gold_version") or "unknown", stamp)
    path = os.path.join(target_dir, name)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(report.to_json())
    return path


def run_evaluation(
    gold_path: Optional[str] = None,
    *,
    injection_path: Optional[str] = None,
    manual_flags: Optional[Mapping[str, bool]] = None,
    store: Optional[RuleStore] = None,
) -> EvalReport:
    """跑一次完整评测并出报告。"""
    gold: GoldSet = load_gold(gold_path)
    eval_sets = derive_eval_sets(gold)
    store = store or RuleStore(seed_rules())
    engine = RuleEngine()
    auditor = Auditor()

    runs = tuple(replay_case(case, store, engine, auditor) for case in gold.cases)
    fact_replays = tuple(
        replay_fact_layer(case) for case in gold.cases if case.field_samples)
    injection = load_injection_cases(injection_path)

    metrics = compute_metric_set(
        runs,
        fact_replays,
        injection,
        store,
        gold_cases={c.case_id: c for c in gold.cases},
        fact_samples={c.case_id: c.field_samples for c in gold.cases if c.field_samples},
    )
    gates = evaluate_gates(metrics, manual_flags=manual_flags)
    verdict = overall_verdict(gates)

    observations = _observations(runs, fact_replays, gold)
    warnings = _sample_size_warnings(gold, eval_sets)

    return EvalReport(
        generated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        gold={
            "gold_version": gold.gold_version,
            "guide_version": gold.guide_version,
            "annotator": gold.annotator,
            "annotated_at": gold.annotated_at,
            "source_path": gold.source_path,
            "rule_store": gold.rule_store,
            "sample_size": eval_sets.sizes,
            "sample_size_warning": warnings,
        },
        metrics=metrics,
        gates=gates,
        verdict=verdict,
        cases=runs,
        fact_replays=fact_replays,
        injection=injection,
        observations=observations,
    )


def _sample_size_warnings(gold: GoldSet, eval_sets: Any) -> Tuple[str, ...]:
    """样本量提醒：小于 PRD 12.3 正式口径时必须写明是「口径试算」。"""
    target = {"EVAL-REV": 200, "EVAL-VER": 200, "EVAL-FACT": 120}
    out: List[str] = []
    for name, size in eval_sets.sizes.items():
        want = target.get(name)
        if want is None:
            continue
        if size < want:
            out.append(
                "%s 当前 %d 件，PRD 12.3 正式口径为 %d 件 → 本次结果属**口径试算**，"
                "正式判定待放量" % (name, size, want))
    return tuple(out)


def _observations(
    runs: Sequence[CaseRun],
    fact_replays: Sequence[FactReplay],
    gold: GoldSet,
) -> Tuple[str, ...]:
    """评测过程中观察到的、**不属于本切片修复范围**的事项。

    这些不是缺陷判定，是给评审方与后续切片留的线索。
    """
    out: List[str] = []

    # 意见文案对「不命中」的措辞：模板理由以「触发依据：」开头，
    # 命中与未命中共用一套措辞——判定正确，但可读性口径待后续统一。
    miss_opinions = 0
    for run in runs:
        for op in run.opinions:
            if op.rationale.startswith("触发依据："):
                miss_opinions += 1
    if miss_opinions:
        out.append(
            "观察：%d 条意见的理由文案统一以「触发依据：」开头，"
            "命中与未命中共用该措辞。判定与严重度不受影响，"
            "文案区分留给后续切片（切片 4 文案口径），本切片不改。" % miss_opinions)

    # 事实层：勾稽类样本走的是规则层字段对，事实层勾稽字段未覆盖
    out.append(
        "口径说明：金标「勾稽冲突」样本的字段对为 monthly_revenue / declared_revenue，"
        "属规则层勾稽（R-CR-013）；事实层勾稽字段（flow_6m_total、approval_limit）"
        "尚无样本，Q-08 因此记未评测（见附录待补项）。")

    # 金标 facts 与 field_samples 的两层视角
    out.append(
        "口径说明：金标 facts 表是**已裁决事实**（进判定链），"
        "field_samples 记录的是**事实层双路抽取**；本切片按两层视角分别评测，"
        "详见 eval/loader.py 文档字符串。")
    return tuple(out)
