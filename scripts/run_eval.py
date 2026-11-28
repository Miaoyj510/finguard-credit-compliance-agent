# -*- coding: utf-8 -*-
"""FinGuard 评测跑批 CLI —— 「跑一下评测，看看现在什么水平」。

用法
----
    python scripts/run_eval.py                 跑一次评测并打印指标表与 Gate 表
    python scripts/run_eval.py --strict        一票否决 Gate 未全通过时退出码 1（CI 用）
    python scripts/run_eval.py --out DIR       指定报告输出目录（默认 reports/eval/）
    python scripts/run_eval.py --gold PATH     指定金标文件（默认 data/gold/eval_rev_batch1.json）

退出码
------
    0 = 评测跑完（**不代表达标**：判定看打印出来的 Gate 表）
    1 = --strict 且存在未通过的一票否决 Gate，或运行出错

⚠ 这个脚本**不新增**任何判定逻辑：指标怎么算、Gate 怎么判，
一律以 src/finguard/eval/ 为准，这里只做「取数 + 打印 + 落盘」。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reconfigure_streams() -> None:
    for _stream in ("stdout", "stderr"):
        try:
            getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _c(text, color, bold=False):
    return "%s%s%s" % (color, text, BOLD if bold else RESET)


def _status_mark(status):
    if status == "pass":
        return _c("达标", GREEN, True)
    if status == "fail":
        return _c("不达标", RED, True)
    return _c("未评测", YELLOW, True)


def main(argv=None):
    _reconfigure_streams()
    parser = argparse.ArgumentParser(description="FinGuard 评测跑批")
    parser.add_argument("--gold", default=None, help="金标文件路径")
    parser.add_argument("--out", default=None, help="报告输出目录")
    parser.add_argument("--strict", action="store_true",
                        help="一票否决 Gate 未全通过时退出码 1")
    args = parser.parse_args(argv)

    from finguard.eval.runner import run_evaluation, write_report

    try:
        report = run_evaluation(args.gold)
    except Exception as exc:                      # 跑批失败必须看得见
        print("评测运行失败：%s: %s" % (exc.__class__.__name__, exc))
        return 1

    gold = report.gold
    print(_c("FinGuard 评测报告", BOLD, True))
    print("金标版本：%s（规范书 %s，标注人 %s）"
          % (gold.get("gold_version"), gold.get("guide_version"),
             gold.get("annotator")))
    print("规则库：%s" % gold.get("rule_store"))
    sizes = gold.get("sample_size", {})
    print("样本量：EVAL-REV %s 件 / EVAL-VER %s 组 / EVAL-FACT %s 字段"
          % (sizes.get("EVAL-REV"), sizes.get("EVAL-VER"), sizes.get("EVAL-FACT")))
    for text in gold.get("sample_size_warning", ()):  # 口径提醒必须在最显眼处
        print(_c("  ⚠ %s" % text, YELLOW))
    print("-" * 92)

    print(_c("指标", BOLD, True))
    for metric in report.metrics.metrics.values():
        print("  %-8s %-22s %-16s %s"
              % (metric.metric_id, metric.name[:20], metric.actual_text(),
                 _status_mark(metric.status)))
    print("-" * 92)

    print(_c("上线门槛 Gate", BOLD, True))
    for gate in report.gates:
        flag = "一票否决" if gate.blocking else "阶段门槛"
        print("  %-8s %-14s [%s] %s"
              % (gate.gate_id, gate.name, flag, _status_mark(gate.status)))
        for item in gate.items:
            if item.status != "pass":
                print("      · %s → %s" % (item.requirement, item.actual))
    print("-" * 92)

    verdict = report.verdict
    print("上线判定：%s" % _c(verdict["上线判定"],
                              GREEN if verdict["上线判定"] == "可上线" else RED, True))
    print("下一阶段判定：%s" % verdict["下一阶段判定"])
    for note in verdict["说明"]:
        print("  · %s" % note)

    if report.observations:
        print("-" * 92)
        print(_c("观察事项", BOLD, True))
        for text in report.observations:
            print("  · %s" % text)

    path = write_report(report, args.out)
    print("-" * 92)
    print("报告已存档：%s" % os.path.relpath(path, ROOT))

    if args.strict:
        blockers = [g.gate_id for g in report.gates
                    if g.blocking and g.status != "pass"]
        if blockers:
            print(_c("严格模式：存在未通过的一票否决 Gate %s，退出码 1" % blockers,
                      RED, True))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
