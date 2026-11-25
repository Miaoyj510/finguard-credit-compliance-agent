#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FinGuard 验证入口 —— 全项目唯一的「跑一下验证效果」命令。

用法
----
    python scripts/verify.py                      跑全部已启用套件（累积回归）
    python scripts/verify.py --list               查看切片与套件状态矩阵
    python scripts/verify.py --slice 1            只跑第 1 切片的套件
    python scripts/verify.py --suite rules        只跑指定套件（可逗号分隔）
    python scripts/verify.py --checkpoint commit  检查点：提交前（累积回归）
    python scripts/verify.py --checkpoint daily   检查点：每日收工（同累积回归）
    python scripts/verify.py --checkpoint milestone 检查点：里程碑（附加 Gate 对照）

退出码：0 = 全部通过；1 = 有失败；2 = 参数或环境错误。

设计约定
--------
1. 本脚本只依赖标准库（unittest），不需要 pip install 任何东西。
2. 套件按「切片」组织，切片越大覆盖越全；新切片只需在 SUITES 里加一行。
3. 未启用的切片显示为「待实现」，**不计入通过率**——避免用空套件稀释结果。
4. 每次新增能力，都必须同时新增对应断言，否则本脚本不会反映真实状态。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import unittest
from typing import Dict, List, Tuple

# ---------------------------------------------------------------- 路径
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
# ROOT：让 tests.* 可被导入；SRC：让 finguard.* 可被导入
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# ---------------------------------------------------------------- 套件登记表
# slice : 切片序号（对应 docs/ROADMAP.md）
# modules: 该切片包含的测试模块；为空表示尚未实现
SUITES: Dict[str, Dict] = {
    "rules": {
        "slice": 1,
        "desc": "规则模型与三键版本绑定（须人工判定）",
        "modules": ["tests.test_rule_model", "tests.test_rule_registry",
                    "tests.test_case_model", "tests.test_verify_entry"],
    },
    "dsl": {
        "slice": 2,
        "desc": "DSL 判定引擎（命中 / 不命中 / 不适用）",
        "modules": [],
    },
    "facts": {
        "slice": 3,
        "desc": "事实层双路抽取、低置信拦截与交叉核对",
        "modules": [],
    },
    "review": {
        "slice": 4,
        "desc": "审查意见：引用三件套、覆盖度、禁止行为",
        "modules": [],
    },
    "harness": {
        "slice": 5,
        "desc": "状态机、Handoff 契约与三处人审闸门",
        "modules": [],
    },
    "replay": {
        "slice": 6,
        "desc": "事件日志、六层卷宗与案件回放",
        "modules": [],
    },
    "post": {
        "slice": 7,
        "desc": "贷后常驻循环、核查与 GATE-C",
        "modules": [],
    },
    "eval": {
        "slice": 8,
        "desc": "评测框架、金标集与 Gate 判定",
        "modules": [],
    },
    "deploy": {
        "slice": 9,
        "desc": "部署形态与生产就绪（灰度 / 回滚 / 压测）",
        "modules": [],
    },
}

SLICE_TITLES = {
    1: "规则模型与三键版本绑定",
    2: "DSL 判定引擎",
    3: "事实层（双路抽取与低置信拦截）",
    4: "审查意见与引用闸门",
    5: "Harness 编排与三处人审闸门",
    6: "事件日志、六层卷宗与回放",
    7: "贷后循环与核查",
    8: "评测框架与 Gate 判定",
    9: "部署与生产就绪",
}

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREY = "\033[90m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _c(text: str, color: str, enabled: bool) -> str:
    return "%s%s%s" % (color, text, RESET) if enabled else text


class Collector(unittest.TextTestResult):
    """收集每个用例的结果，用于逐条打印。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rows: List[Tuple[str, str, str]] = []   # (module, test, status)

    # Python 3.9 的签名是 addXxx(self, test)，3.10+ 是 addXxx(self, test, err)。
    # 这里用 try/except 兼容两种，避免在不同机器上跑验证时炸掉。
    def addSuccess(self, test, err=None):                 # type: ignore[override]
        try:
            super().addSuccess(test, err)
        except TypeError:
            super().addSuccess(test)
        self.rows.append((test.__class__.__module__, test._testMethodName, "PASS"))

    def addFailure(self, test, err=None):                # type: ignore[override]
        try:
            super().addFailure(test, err)
        except TypeError:
            super().addFailure(test)
        self.rows.append((test.__class__.__module__, test._testMethodName, "FAIL"))

    def addError(self, test, err=None):                  # type: ignore[override]
        try:
            super().addError(test, err)
        except TypeError:
            super().addError(test)
        self.rows.append((test.__class__.__module__, test._testMethodName, "ERROR"))

    def addSkip(self, test, reason):                # type: ignore[override]
        super().addSkip(test, reason)
        self.rows.append((test.__class__.__module__, test._testMethodName, "SKIP"))


def enabled_suites() -> List[str]:
    return [name for name, cfg in SUITES.items() if cfg["modules"]]


def build_suite(names: List[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        for module in SUITES[name]["modules"]:
            suite.addTests(loader.loadTestsFromNames([module]))
    return suite


def print_matrix() -> None:
    print(_c("%s  切片 / 套件状态矩阵%s" % (BOLD, RESET), "", True))
    print("-" * 78)
    print("%-4s %-34s %-14s %s" % ("切片", "主题", "套件", "状态"))
    print("-" * 78)
    for name, cfg in SUITES.items():
        state = _c("已启用", GREEN, True) if cfg["modules"] else _c("待实现", GREY, True)
        print("%-4d %-34s %-14s %s" % (
            cfg["slice"], SLICE_TITLES.get(cfg["slice"], ""), name, state))
    print("-" * 78)


def print_milestone_gate_table() -> None:
    print()
    print(_c("里程碑 Gate 对照（切片 8 起启用，当前仅显示 PRD 基线）", BOLD, True))
    print("-" * 78)
    rows = [
        ("GATE-1 正确性", "Q-01=0 / Q-02=100% / Q-03=100% / Q-04=100% / Q-07=100% / Q-09=100%", "一票否决"),
        ("GATE-2 安全", "SEC-01~SEC-09 全部达标（5 项为 0 或 100%）", "一票否决"),
        ("GATE-3 人在环", "闸门绕行=0 / 闸门及时率≥90% / 签核含双版本", "一票否决"),
        ("GATE-4 质量下限", "Q-05≥95% / Q-06≥95% / Q-08≥95%", "一票否决"),
        ("GATE-5 业务价值", "P-01≥85% / P-03≥60% / P-04≥30 天 / P-10=100%", "不达标不推进"),
        ("GATE-6 性能", "C-01 达标（P95≤10 分钟）+ Demo 形态 1—3 用户可完成端到端", "不达标不试点"),
        ("GATE-7 回归", "规则/模型/提示词变更后回归集全通过", "阻断发布"),
        ("GATE-8 生产就绪", "部署可重复 / 灰度回滚 / 50 并发压测 / PP-1~PP-8 / RTO-RPO / 告警 / Runbook", "不得进入生产试点"),
    ]
    for g, content, effect in rows:
        print("%-16s %-62s %s" % (g, content, effect))
    print("-" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="FinGuard 验证入口")
    ap.add_argument("--list", action="store_true", help="列出切片与套件状态")
    ap.add_argument("--slice", type=int, default=None, help="只跑指定切片的套件")
    ap.add_argument("--suite", default=None, help="只跑指定套件（逗号分隔）")
    ap.add_argument("--checkpoint", choices=["commit", "daily", "milestone"], default=None,
                    help="检查点：commit / daily 跑累积回归；milestone 附加 Gate 对照")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印失败详情")
    args = ap.parse_args()

    if args.list:
        print_matrix()
        return 0

    if args.suite:
        names = [s.strip() for s in args.suite.split(",") if s.strip()]
        unknown = [n for n in names if n not in SUITES]
        if unknown:
            print("未知套件：%s" % ", ".join(unknown))
            return 2
    elif args.slice is not None:
        names = [n for n, c in SUITES.items() if c["slice"] == args.slice]
        if not names:
            print("切片 %s 不存在" % args.slice)
            return 2
    else:
        names = enabled_suites()

    # 设计约定 3：未启用的套件不计入通过率。显式请求未实现的切片/套件时
    # 不得跑空套件打印「0 / 0 全部通过」——那是假绿，退出码必须非 0。
    pending = [n for n in names if not SUITES[n]["modules"]]
    names = [n for n in names if SUITES[n]["modules"]]
    if pending:
        print("待实现套件（不计入通过率）：%s" % ", ".join(pending))
    if not names:
        if args.slice is not None or args.suite:
            print("所请求的切片/套件尚未实现，没有可运行的断言——不输出通过结果。")
            return 2
        print("没有可运行的套件——先实现一个切片再验证。")
        print_matrix()
        return 0

    print(_c("%sFinGuard 验证%s" % (BOLD, RESET), "", True))
    print("运行套件：%s" % ", ".join(names))
    print("-" * 78)

    suite = build_suite(names)
    runner = unittest.TextTestRunner(resultclass=Collector, verbosity=0, stream=io.StringIO())
    result = runner.run(suite)

    current_module = None
    for module, test, status in result.rows:
        if module != current_module:
            current_module = module
            print()
            print(_c("  %s" % module, BOLD, True))
        mark = {"PASS": _c("  ✓", GREEN, True),
                "FAIL": _c("  ✗", RED, True),
                "ERROR": _c("  !", RED, True),
                "SKIP": _c("  -", YELLOW, True)}[status]
        print("%s %s" % (mark, test))

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed - len(result.skipped)

    print()
    print("-" * 78)
    if failed:
        print(_c("结果：%d / %d 通过，%d 失败" % (passed, total, failed), RED, True))
    else:
        print(_c("结果：%d / %d 全部通过" % (total, total), GREEN, True))

    if args.verbose and result.failures:
        print()
        for test, tb in result.failures:
            print(_c("失败详情：%s.%s" % (test.__class__.__name__, test._testMethodName), RED, True))
            print(tb)
    if args.verbose and result.errors:
        print()
        for test, tb in result.errors:
            print(_c("错误详情：%s.%s" % (test.__class__.__name__, test._testMethodName), RED, True))
            print(tb)

    if args.checkpoint == "milestone":
        print_milestone_gate_table()

    print()
    print("检查点建议：改完任何一层都跑一次；切片的验收标准见 docs/ROADMAP.md。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
