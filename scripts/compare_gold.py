#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""金标标注校验与双盲比对（切片 8.0）。

三种用法：

    # 1) 校验一份标注是否合法（字段齐、取值在枚举内）
    python scripts/compare_gold.py --check data/gold/labels/impl-agent.jsonl

    # 2) 比对两份标注：裁判间一致率 + 分歧清单
    python scripts/compare_gold.py --left A.jsonl --right B.jsonl

    # 3) 再叠加与**技术真值**的一致率（裁判识读 / 规则适用性是否判对）
    python scripts/compare_gold.py --left A.jsonl --right B.jsonl --truth truth.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from finguard.eval.gold import (            # noqa: E402
    Annotation,
    AnnotationError,
    agreement_report,
    validate_annotation,
)


def load_annotations(path):
    out = []
    with io.open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError as exc:
                raise AnnotationError("%s 第 %d 行不是合法 JSON：%s" % (path, lineno, exc))
            annotation = Annotation.from_dict(data)
            try:
                validate_annotation(annotation)
            except AnnotationError as exc:
                raise AnnotationError("%s 第 %d 行：%s" % (path, lineno, exc))
            out.append(annotation)
    return out


def load_truth(path):
    truth = {}
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            truth[data["case_id"]] = data
    return truth


#: 分歧仲裁提示：哪个字段以谁为准
ADJUDICATION_HINTS = {
    "rule_applicability": "以技术真值为准（生成器用的就是它）",
    "severity_appropriate": "以规则库为准；若规则库档位在此情形不适用，记为规则改进线索",
    "fact_recognition_ok": "以材料原文为准，重读材料",
    "reason_quality": "以材料能否支撑结论为准",
    "is_false_positive": "以核查证据为准；证据不足时取「无法判断」",
}


def truth_agreement(annotations, truth):
    """裁判 vs 技术真值：识读正确率与规则适用性认同率。"""
    fact_ok = fact_total = 0
    rule_ok = rule_total = 0
    for annotation in annotations:
        case = truth.get(annotation.case_id)
        if case is None:
            continue
        fact_total += 1
        if annotation.labels.get("fact_recognition_ok") == "对":
            fact_ok += 1
        rule_total += 1
        if annotation.labels.get("rule_applicability") == "同意":
            rule_ok += 1
    return {
        "cases": fact_total,
        "fact_recognition_correct": fact_ok,
        "fact_recognition_rate": round(fact_ok / fact_total, 6) if fact_total else None,
        "rule_applicability_agree": rule_ok,
        "rule_applicability_rate": round(rule_ok / rule_total, 6) if rule_total else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="金标标注校验与双盲比对")
    ap.add_argument("--check", default=None, help="只校验这一份标注")
    ap.add_argument("--left", default=None)
    ap.add_argument("--right", default=None)
    ap.add_argument("--truth", default=None)
    ap.add_argument("--out", default=None, help="把比对结果写成 JSON")
    args = ap.parse_args()

    if args.check:
        annotations = load_annotations(args.check)
        print("OK %s：%d 份标注全部合法" % (args.check, len(annotations)))
        return 0

    if not (args.left and args.right):
        print("需要 --left 与 --right（或用 --check 校验单份）")
        return 2

    left = load_annotations(args.left)
    right = load_annotations(args.right)
    report = agreement_report(left, right, labels=ADJUDICATION_HINTS)

    if args.truth:
        truth = load_truth(args.truth)
        report["left_vs_truth"] = truth_agreement(left, truth)
        report["right_vs_truth"] = truth_agreement(right, truth)

    print("=" * 72)
    print("双盲比对：%s（%d 件） vs %s（%d 件）"
          % (os.path.basename(args.left), len(left),
             os.path.basename(args.right), len(right)))
    print("=" * 72)
    print("案件级一致率：%s（%d / %d）"
          % (report["case_agreement_rate"], report["cases_agree"], report["cases_compared"]))
    print("字段级一致率：%s（%d / %d）"
          % (report["field_agreement_rate"], report["fields_agree"], report["fields_compared"]))
    print("-" * 72)
    for name, stats in sorted(report["per_field"].items()):
        print("  %-22s %-6s (%d / %d)" % (name, stats["rate"], stats["agree"], stats["total"]))
    if "left_vs_truth" in report:
        print("-" * 72)
        for side in ("left_vs_truth", "right_vs_truth"):
            info = report[side]
            print("  %-14s 识读正确率 %s · 规则适用性认同率 %s"
                  % (side, info["fact_recognition_rate"], info["rule_applicability_rate"]))
    print("-" * 72)
    print("分歧 %d 项，需人工仲裁：" % report["diff_count"])
    for diff in report["diffs"]:
        print("  %-14s %-22s %s | %s"
              % (diff["case_id"], diff["field"], diff["left"], diff["right"]))
        if diff.get("adjudication_hint"):
            print("      仲裁提示：%s" % diff["adjudication_hint"])

    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        print("比对结果已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())