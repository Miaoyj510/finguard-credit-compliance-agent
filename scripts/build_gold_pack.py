#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成金标标注任务包（切片 8.0）。

产出**三个文件**，刻意把"给裁判的"与"给系统比对的"分开：

    materials.jsonl  每行一件案件的**任务包**：只有材料 + 待标字段 + 取值枚举
                     **不含任何真值**——裁判看不到答案
    truth.jsonl      每行一件案件的**技术性真值**：事实取值、规则应命中状态、
                     适用版本、覆盖度三类计数、未判定原因码（供系统比对）
    manifest.json    seed、生成时间、规则集指纹、件数、案件号清单

规则集指纹很重要：两个 Agent 必须在**同一版规则**上标注，
否则分歧可能只是"规则变了"，而不是"看法不同"。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from finguard.eval.synth import generate_cases          # noqa: E402
from finguard.rules.seeds import RULES as CREDIT_RULES  # noqa: E402


def rules_view(rules):
    """按 rule_id 分组的规则清单。

    裁判要判断「规则适用性」，必须知道有哪些规则、适用范围、生效区间与严重度。
    **规则本身对所有审查岗可读**，给出它不泄漏任何判定结果——
    真正不能给裁判的是「本案该规则判成了什么」，那在 truth.jsonl 里。
    """
    grouped = {}
    for rule in rules:
        entry = grouped.setdefault(rule.rule_id, {
            "rule_id": rule.rule_id,
            "versions": [],
            "product_scope": list(rule.product_scope),
            "org_scope": list(rule.org_scope),
            "required_facts": list(rule.required_facts),
            "dsl": rule.dsl,
        })
        entry["versions"].append({
            "version": rule.version,
            "effective_from": rule.effective_from.isoformat(),
            "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
            "severity": rule.severity.value,
            "source_ref": rule.source_ref,
        })
    for entry in grouped.values():
        entry["versions"].sort(key=lambda v: (v["effective_from"], v["version"]))
    return [grouped[k] for k in sorted(grouped)]


def rules_fingerprint(rules) -> str:
    body = "|".join("%s@%s:%s" % (r.rule_id, r.version, r.dsl) for r in rules)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="生成金标标注任务包")
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261128)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "gold", "pack"))
    ap.add_argument("--prefix", default="FG-G", help="案件号前缀；不同批次用不同前缀")
    args = ap.parse_args()

    rules = sorted(CREDIT_RULES, key=lambda r: (r.rule_id, r.version))
    cases = generate_cases(rules, count=args.count, seed=args.seed,
                          case_prefix=args.prefix)

    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    view = rules_view(rules)
    with io.open(os.path.join(args.out, "materials.jsonl"), "w", encoding="utf-8") as fh:
        for case in cases:
            pack = case.task_pack()
            pack["rules"] = view
            fh.write(json.dumps(pack, ensure_ascii=False) + "\n")

    with io.open(os.path.join(args.out, "truth.jsonl"), "w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case.ground_truth(), ensure_ascii=False) + "\n")

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "count": len(cases),
        "case_ids": [c.case_id for c in cases],
        "rules_fingerprint": rules_fingerprint(rules),
        "rules": ["%s@%s" % (r.rule_id, r.version) for r in rules],
        "split": "任务包（materials）与技术真值（truth）分离；裁判只读任务包",
        "blind_batch": True,
        "blind_note": (
            "盲标批次要求：该批次的真值**未被任何裁判接触过**。"
            "实现方在开发生成器时必然见过早期批次的真值，"
            "因此换批次（不同 seed / 前缀）后该批次才可作盲标样本。"),
    }
    with io.open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print("已生成 %d 件案件 -> %s" % (len(cases), args.out))
    print("  materials.jsonl  任务包（裁判读这个）")
    print("  truth.jsonl      技术真值（系统比对读这个）")
    print("  manifest.json    指纹 %s" % manifest["rules_fingerprint"][:23])
    return 0


if __name__ == "__main__":
    sys.exit(main())