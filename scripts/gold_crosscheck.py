#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标注人 A：提供**系统输出作对照**（金标标注规范书 v1 第 0 节）。

评审方（标注人 B）已手工推演出 EVAL-REV 首批 30 件的技术性金标
（``data/gold/eval_rev_batch1.json``）。本脚本把**管线**对这 30 件的判定
跑出来，与 B 的金标**逐件逐字段对照**，产出分歧清单。

    · 判定与金标不一致 -> 要么金标笔误，要么管线缺陷 -> 走 ARBITRATION.md 的
      G / S / D 三类仲裁
    · 交叉校验是 **QA 机制**，不是标注来源（B 的金标是手工推演，不来自管线）

⚠ 绑定分类**不复用** ``RuleStore.bind()`` 的粗分类（它把"范围排除"与
"时间版本缺口"都归为 NO_VERSION_MATCH），而是按**金标口径**重写一遍：
范围排除 -> 不适用；范围适用但无唯一版本 -> 须人工判定。
这是口径对齐，不是"照抄金标"——金标本身是手工推演的结果。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from finguard.rules.engine import RuleEngine           # noqa: E402
from finguard.rules.registry import RuleStore          # noqa: E402
from finguard.rules.seeds import RULES as CREDIT_RULES  # noqa: E402


def bind_per_prd(store, triple_key):
    """按**金标口径**逐 rule_id 绑定：范围排除 / 无唯一版本 / 唯一命中。"""
    business_date = triple_key["business_date"]
    product = triple_key["product_type"]
    org = triple_key["org_level"]
    out = []
    for rule_id in sorted({r.rule_id for r in CREDIT_RULES}):
        versions = [r for r in store.versions_of(rule_id)]
        probe = versions[0]
        if not (probe.covers_product(product) and probe.covers_org(org)):
            # 范围排除同样是「须人工判定」（切片 1 ROADMAP 验收标准），
            # 原因码 NO_VERSION_MATCH——与 RuleStore.bind() 的口径一致。
            out.append({"rule_id": rule_id, "version": None,
                        "basis": "须人工判定", "reason_code": "NO_VERSION_MATCH"})
            continue
        effective = [r for r in versions
                     if r.effective_from.isoformat() <= business_date
                     and (r.effective_to is None
                          or business_date < r.effective_to.isoformat())]
        if len(effective) == 0:
            out.append({"rule_id": rule_id, "version": None,
                        "basis": "须人工判定", "reason_code": "NO_VERSION_MATCH"})
        elif len(effective) > 1:
            out.append({"rule_id": rule_id, "version": None,
                        "basis": "须人工判定", "reason_code": "AMBIGUOUS_VERSION"})
        else:
            out.append({"rule_id": rule_id, "version": effective[0].version,
                        "basis": "三键唯一命中", "reason_code": None})
    return out


def run_case(store, engine, case):
    """跑管线：绑定 + 判定 + 覆盖度（PRD 口径：绑定级未判定计入未判定）。"""
    triple_key = case["triple_key"]
    bindings = bind_per_prd(store, triple_key)
    facts = dict(case.get("facts") or {})
    missing = set(case.get("missing_facts") or ())
    verdicts = []
    judged = not_applicable = unresolved = 0
    for b in bindings:
        if b["basis"] == "范围不适用":
            not_applicable += 1
            continue
        if b["basis"] == "须人工判定":
            unresolved += 1
            continue
        rule = store.get(b["rule_id"], b["version"])
        evaluation = engine.evaluate(rule, facts)
        if evaluation.coverage.value == "未判定":
            unresolved += 1
            code = (evaluation.reason_code.value
                    if evaluation.reason_code else "MISSING_FACT")
            verdicts.append({"rule_id": b["rule_id"], "version": b["version"],
                             "verdict": "unresolved", "severity": None,
                             "fact_refs": list(evaluation.fact_refs),
                             "reason_code": code})
            continue
        judged += 1
        verdicts.append({
            "rule_id": b["rule_id"], "version": b["version"],
            "verdict": evaluation.verdict.value,
            "severity": rule.severity.value,
            "fact_refs": list(evaluation.fact_refs),
            "reason_code": None,
        })
    return {
        "binding": bindings,
        "verdicts": verdicts,
        "coverage_prd": {"judged": judged, "not_applicable": not_applicable,
                         "unresolved": unresolved,
                         "total": len(bindings)},
    }


def compare(gold_case, sys_out):
    """逐件比对，返回分歧清单。"""
    diffs = []
    gold_bind = {b["rule_id"]: b for b in gold_case["gold"]["binding"]}
    sys_bind = {b["rule_id"]: b for b in sys_out["binding"]}
    for rule_id in sorted(set(gold_bind) | set(sys_bind)):
        g = gold_bind.get(rule_id, {})
        s = sys_bind.get(rule_id, {})
        for field in ("version", "basis", "reason_code"):
            gv = g.get(field)
            sv = s.get(field)
            if gv != sv:
                diffs.append({"kind": "binding", "rule_id": rule_id,
                              "field": field, "gold": gv, "system": sv})
    gold_verdicts = {v["rule_id"]: v for v in gold_case["gold"]["verdicts"]}
    sys_verdicts = {v["rule_id"]: v for v in gold_case["gold"]["verdicts"]}
    for rule_id in sorted(set(gold_verdicts) | set(sys_verdicts)):
        g = gold_verdicts.get(rule_id, {})
        s = sys_verdicts.get(rule_id, {})
        for field in ("verdict", "severity"):
            if g.get(field) != s.get(field):
                diffs.append({"kind": "verdict", "rule_id": rule_id,
                              "field": field, "gold": g.get(field),
                              "system": s.get(field)})
    gc = gold_case["gold"]["coverage_prd"]
    sc = sys_out["coverage_prd"]
    for field in ("judged", "not_applicable", "unresolved", "total"):
        if gc.get(field) != sc.get(field):
            diffs.append({"kind": "coverage", "rule_id": "-", "field": field,
                          "gold": gc.get(field), "system": sc.get(field)})
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description="标注人 A：系统输出作对照")
    ap.add_argument("--gold", default=os.path.join(ROOT, "data", "gold",
                                                   "eval_rev_batch1.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "gold",
                                                  "crosscheck_batch1.json"))
    args = ap.parse_args()

    with io.open(args.gold, encoding="utf-8") as fh:
        gold = json.load(fh)
    cases = gold["cases"]

    store = RuleStore(sorted(CREDIT_RULES, key=lambda r: (r.rule_id, r.version)))
    engine = RuleEngine()

    rows = []
    total_diffs = 0
    for case in cases:
        sys_out = run_case(store, engine, case)
        diffs = compare(case, sys_out)
        total_diffs += len(diffs)
        rows.append({"case_id": case["case_id"], "bucket": case.get("bucket"),
                     "diffs": diffs, "system": sys_out})

    report = {
        "role": "标注人 A · 系统输出作对照（规范书 v1 第 0 节）",
        "gold_version": gold.get("gold_version"),
        "gold_file": os.path.relpath(args.gold, ROOT),
        "cases_compared": len(cases),
        "cases_agree": sum(1 for r in rows if not r["diffs"]),
        "total_diffs": total_diffs,
        "rows": rows,
    }
    with io.open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print("对照 %d 件：一致 %d 件，分歧 %d 项 -> %s"
          % (report["cases_compared"], report["cases_agree"], total_diffs,
             os.path.relpath(args.out, ROOT)))
    for row in rows:
        if row["diffs"]:
            print("  %-14s %s" % (row["case_id"],
                                  " / ".join("%s.%s: gold=%s sys=%s"
                                             % (d["kind"], d["field"], d["gold"], d["system"])
                                             for d in row["diffs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())