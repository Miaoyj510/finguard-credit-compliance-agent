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

def _reconfigure_streams() -> None:
    """把标准流切到 utf-8。

    放在 main() 里而不是模块顶层：否则 `import verify` 会改动调用方的输出编码
    （评审意见 11）。被当作库导入时不产生任何副作用。
    """
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
        "modules": ["tests.test_dsl", "tests.test_engine"],
    },
    "facts": {
        "slice": 3,
        "desc": "事实层双路抽取、低置信拦截与交叉核对",
        "modules": ["tests.test_facts_extract", "tests.test_facts_crosscheck",
                    "tests.test_facts_conflict", "tests.test_facts_layer",
                    "tests.test_facts_adjudication"],
    },
    "review": {
        "slice": 4,
        "desc": "审查意见：引用三件套、覆盖度、禁止行为",
        "modules": ["tests.test_opinion", "tests.test_auditor"],
    },
    "harness": {
        "slice": 5,
        "desc": "状态机、Handoff 契约与三处人审闸门",
        "modules": ["tests.test_harness_states", "tests.test_handoff",
                    "tests.test_gates", "tests.test_orchestrator",
                    "tests.test_audit_probe", "tests.test_harness_gate_gaps",
                    "tests.test_gate_c1_escalation"],
    },
    "replay": {
        "slice": 6,
        "desc": "事件日志、六层卷宗与案件回放",
        "modules": ["tests.test_event_log", "tests.test_dossier", "tests.test_replay",
                    "tests.test_slice6_contracts"],
    },
    "data": {
        "slice": 6.5,
        "desc": "外部数据集固化（SHA256 钉死）与参数校准（只出相对量）",
        "modules": ["tests.test_data_prep"],
    },
    "post": {
        "slice": 7,
        "desc": "贷后常驻循环、核查初核与 GATE-C 处置四档",
        "modules": ["tests.test_post_monitor", "tests.test_verifier",
                    "tests.test_post_flow"],
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
    6.5: "外部数据集固化与参数校准",
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


def run_harness_security_probe():
    """切片 5 安全探针：现场实测三处闸门**不可绕过、不可代签**。

    这不是跑断言，而是**把绕过尝试真的打一遍**，把结果打成一张表：
    跑的人能直接看到"我试了 5 条绕路，全部被拦"，而不是只看到一个绿色勾。

    返回 (rows, ok)：
        rows —— 每条探针一行（指标 / 口径 / 目标 / 实测 / 结论）
        ok   —— 是否全部达标（不达标时退出码非 0）
    """
    from datetime import date

    from finguard.agents.harness.gates import GateState, Signer
    from finguard.agents.harness.orchestrator import Harness
    from finguard.agents.harness.states import (
        GateBypassError,
        GateSignError,
        Stage,
        StageMutationError,
    )
    from finguard.domain.case import TripleKey
    from finguard.domain.case_state import CaseFile

    def make_case():
        return CaseFile(
            case_id="FG-PROBE-000001",
            triple_key=TripleKey(date(2026, 10, 28), "\u5c0f\u989d\u7ecf\u8425\u8d37", "\u4e8c\u7ea7\u5206\u884c"),
        )

    def make_signer(**overrides):
        data = dict(
            account_id="probe-001",
            display_name="\u63a2\u9488\u7b7e\u6838\u4eba",
            role="\u5185\u63a7\u5408\u89c4\u5ba1\u67e5\u5c97",
            authority_level=2,
            authorized_case_types=("corp_credit_review",),
            device_fingerprint="probe-fp",
            ip="10.0.0.9",
        )
        data.update(overrides)
        return Signer(**data)

    rows = []

    # --- 探针 1：跳过 GATE-A 直接进 S4 ---
    h = Harness(make_case(), clock=lambda: "2026-11-26T10:00:00")
    h.auto_advance()
    blocked = False
    try:
        h.transition(Stage.S4, actor="\u7f16\u6392\u5668")
    except GateBypassError:
        blocked = True
    rows.append(("SEC-03 \u95f8\u95e8\u7ed5\u884c", "\u672a\u7b7e\u6838\u5373\u8fdb\u5165\u653e\u884c\u540e\u7ee7\u72b6\u6001", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    # --- \u63a2\u9488 2\uff1a\u76f4\u63a5\u6539\u5199\u9636\u6bb5\u5b57\u6bb5 ---
    case = make_case()
    blocked = False
    try:
        case.stage = Stage.S4
    except StageMutationError:
        blocked = True
    rows.append(("SEC-03 \u9636\u6bb5\u7be1\u6539", "\u76f4\u63a5\u8d4b\u503c case.stage", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    # --- \u63a2\u9488 3\uff1a\u4f2a\u9020\u5df2\u7b7e\u6838\u72b6\u6001\uff08\u65e0\u7b7e\u6838\u8bc1\u636e\uff09 ---
    h = Harness(make_case(), clock=lambda: "2026-11-26T10:00:00")
    h.auto_advance()
    h.case.gates["GATE-A"]._state = GateState.APPROVED
    blocked = False
    try:
        h.transition(Stage.S4, actor="\u7f16\u6392\u5668")
    except GateBypassError:
        blocked = True
    rows.append(("SEC-03 \u4f2a\u9020\u72b6\u6001", "\u6539\u72b6\u6001\u5b57\u6bb5\u4f46\u65e0\u7b7e\u6838\u8bc1\u636e", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    # --- \u63a2\u9488 4\uff1a\u6279\u91cf\u5bfc\u5165\u5f0f\u4ee3\u7b7e ---
    h = Harness(make_case(), clock=lambda: "2026-11-26T10:00:00")
    h.auto_advance()
    blocked = False
    try:
        h.sign_gate("GATE-A", signer=make_signer(batch_mode=True))
    except GateSignError:
        blocked = True
    rows.append(("SEC-04 \u4ee3\u7b7e\u5c1d\u8bd5", "\u6279\u91cf\u5bfc\u5165\u5f0f\u7b7e\u6838", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    # --- \u63a2\u9488 5\uff1a\u89c4\u5219\u53d1\u5e03\u4eba\u4ee3\u7b7e ---
    h = Harness(make_case(), clock=lambda: "2026-11-26T10:00:00")
    h.auto_advance()
    blocked = False
    try:
        h.sign_gate("GATE-A", signer=make_signer(is_rule_author=True))
    except GateSignError:
        blocked = True
    rows.append(("SEC-04 \u804c\u8d23\u5206\u79bb", "\u89c4\u5219\u53d1\u5e03\u4eba\u7b7e\u6838", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    # --- \u63a2\u9488 6\uff1a\u8d85\u65f6\u540e\u81ea\u52a8\u653e\u884c ---
    h = Harness(make_case(), clock=lambda: "2026-11-26T10:00:00")
    h.auto_advance()
    h.escalate_overdue("2026-11-28T10:00:00")
    blocked = False
    try:
        h.transition(Stage.S4, actor="\u7f16\u6392\u5668")
    except GateBypassError:
        blocked = True
    pending = h.case.gates["GATE-A"].state is GateState.PENDING
    rows.append(("FR-GATE-04 \u8d85\u65f6\u4e0d\u653e\u884c", "\u8d85\u65f6\u5347\u7ea7\u540e\u4ecd\u4e0d\u653e\u884c", "0",
                 ("\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a") + ("\uff08\u4ecd\u4e3a PENDING\uff09" if pending else ""),
                 blocked and pending))

    ok = all(row[4] for row in rows)
    return rows, ok


def _pad(text, width):
    """按**显示宽度**左对齐补空格（中文算 2 格），让表格列对齐。"""
    import unicodedata
    size = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(1, width - size)


def run_replay_probe():
    """切片 6 现场探针：跑一条完整链路，看回放能不能复现当时的结论。

    这不是跑断言，而是**真的走一遍案件**，把四个关键结论打成表：
    哈希链完整、六层齐全、同案同起点重放一致、篡改能检出。
    """
    from datetime import date

    from finguard.agents.harness.gates import Signer
    from finguard.agents.harness.orchestrator import Harness
    from finguard.agents.harness.states import Stage
    from finguard.agents.replay import ReplayEngine
    from finguard.domain.case import TripleKey
    from finguard.domain.case_state import CaseFile
    from finguard.domain.enums import CoverageFlag, Severity
    from finguard.domain.opinion import ReviewOpinion
    from finguard.facts.extract import RouteResult
    from finguard.facts.layer import FactLayer
    from finguard.facts.models import SourceChannel
    from finguard.storage.dossier import (
        Dossier,
        DossierLayer,
        disposition_layer_payload,
        documents_layer_payload,
        facts_layer_payload,
        opinions_layer_payload,
        rules_layer_payload,
        signatures_layer_payload,
    )
    from finguard.storage.event_log import EventLog

    case_id = "FG-PROBE-000002"
    now = "2026-11-26T10:00:00"

    def route(name, values):
        return RouteResult(route=name, channel=SourceChannel.OCR, values=dict(values))

    log = EventLog(clock=lambda: now)
    case = CaseFile(
        case_id=case_id,
        triple_key=TripleKey(date(2026, 10, 28), "\u5c0f\u989d\u7ecf\u8425\u8d37", "\u4e8c\u7ea7\u5206\u884c"),
    )
    harness = Harness(case, event_sink=log.append_sink(), clock=lambda: now)
    harness.auto_advance()
    opinion = ReviewOpinion(
        opinion_id="OP-%s-001" % case_id,
        rule_id="R-CR-013",
        rule_version="3.2.0",
        severity=Severity.MAJOR,
        fact_refs=("monthly_revenue",),
        rationale="\u6d41\u6c34\u4e0e\u7533\u62a5\u8425\u6536\u504f\u79bb",
        coverage=CoverageFlag.JUDGED,
    )
    harness.record_opinion(opinion)
    harness.sign_gate("GATE-A", signer=Signer(
        account_id="probe-001", display_name="\u63a2\u9488\u7b7e\u6838\u4eba",
        role="\u5185\u63a7\u5408\u89c4\u5ba1\u67e5\u5c97", authority_level=2,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="probe-fp", ip="10.0.0.9",
    ))
    harness.transition(Stage.S4, actor="\u7f16\u6392\u5668")
    harness.auto_advance()
    harness.sign_gate("GATE-B", signer=Signer(
        account_id="probe-002", display_name="\u63a2\u9488\u7814\u5224\u4eba",
        role="\u98ce\u9669\u7814\u5224\u5c97", authority_level=3,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="probe-fp2", ip="10.0.0.10",
    ))
    harness.transition(Stage.S5, actor="\u7f16\u6392\u5668")
    harness.record_disposition("\u8c03\u6574\u5173\u6ce8")
    harness.transition(Stage.S6, actor="\u7f16\u6392\u5668")

    a = {"monthly_revenue": 820000, "declared_revenue": 1000000}
    factset, _q = FactLayer().build(route("vote", a), route("rule_check", a))
    trail = [e["detail"]["to_stage"] for e in harness.events if e["kind"] == "stage_transition"]
    dossier = Dossier(case_id, clock=lambda: now)
    dossier.write(DossierLayer.L1, facts_layer_payload(factset))
    dossier.write(DossierLayer.L2, rules_layer_payload(case.rule_binding, case.unresolved))
    dossier.write(DossierLayer.L3, documents_layer_payload(
        [{"doc_id": "D-1", "chunk_id": "C-1", "source_ref": "\u300a\u5236\u5ea6\u300b", "quote": "\u539f\u6587"}]))
    dossier.write(DossierLayer.L4, opinions_layer_payload([opinion], spec_version="auditor-1.0"))
    dossier.write(DossierLayer.L5, signatures_layer_payload(case.gates, trail))
    dossier.write(DossierLayer.L6, disposition_layer_payload({"action": "\u8c03\u6574\u5173\u6ce8"}))

    engine = ReplayEngine(log, dossier)
    first = engine.replay(case_id, from_seq=1)
    second = engine.replay(case_id, from_seq=1)

    rows = []
    rows.append(("\u4e8b\u4ef6\u65e5\u5fd7\u54c8\u5e0c\u94fe", "\u6574\u94fe\u91cd\u7b97\u6821\u9a8c", "\u901a\u8fc7",
                 "\u901a\u8fc7" if log.verify().ok else "\u65ad\u88c2", log.verify().ok))
    rows.append(("P-10 \u5377\u5b97\u9f50\u5168", "\u516d\u5c42\u5747\u5b58\u5728\u4e14\u54c8\u5e0c\u901a\u8fc7", "\u5b8c\u6574",
                 dossier.verify().completeness.value, dossier.verify().ok))
    rows.append(("\u56de\u653e\u4e00\u81f4\u6027", "\u540c\u6848\u540c\u8d77\u70b9\u4e24\u6b21\u91cd\u653e\u7ed3\u8bba\u76f8\u540c", "\u4e00\u81f4",
                 "\u4e00\u81f4" if first.trace == second.trace else "\u4e0d\u4e00\u81f4", first.trace == second.trace))
    rows.append(("\u5377\u5b97\u5bf9\u8d26", "\u91cd\u653e\u8f68\u8ff9\u4e0e\u5377\u5b97\u8bb0\u5f55\u4e00\u81f4", "\u4e00\u81f4",
                 "\u4e00\u81f4" if first.matches_dossier else "\u4e0d\u4e00\u81f4", first.matches_dossier))
    rows.append(("\u53ef\u53d6\u8bc1\u6027", "\u94fe\u5b8c\u6574 + \u516d\u5c42\u9f50\u5168 + \u8f68\u8ff9\u4e00\u81f4", "\u53ef\u53d6\u8bc1",
                 "\u53ef\u53d6\u8bc1" if first.usable_for_audit else "\u4e0d\u53ef\u53d6\u8bc1", first.usable_for_audit))

    # \u7be1\u6539\u68c0\u51fa\uff1a\u52a8\u4e00\u6761\u4e8b\u4ef6\u518d\u6821\u9a8c
    events = list(log.events(case_id))
    events[1].payload["to_stage"] = "S9"
    tampered = log.verify()
    rows.append(("\u7be1\u6539\u68c0\u51fa", "\u6539\u52a8\u4e00\u6761\u4e8b\u4ef6\u540e\u94fe\u6821\u9a8c\u5fc5\u987b\u5931\u8d25", "0",
                 "\u5df2\u68c0\u51fa" if not tampered.ok else "\u672a\u68c0\u51fa", not tampered.ok))

    ok = all(row[4] for row in rows)
    return rows, ok


def run_post_probe():
    """切片 7 现场探针：贷后闭环能不能守住闸门、算不算得对提前量。"""
    from datetime import date

    from finguard.agents.harness.gates import Signer
    from finguard.agents.harness.orchestrator import Harness
    from finguard.agents.harness.states import GateBypassError, Stage
    from finguard.agents.post_loop import PostFlow
    from finguard.agents.post_monitor import PostMonitorAgent
    from finguard.agents.verifier import VerifierAgent
    from finguard.domain.case import TripleKey
    from finguard.domain.case_state import CaseFile
    from finguard.domain.post import PostCaseView
    from finguard.rules.post_seeds import post_rule_map

    rows = []

    case = CaseFile(
        case_id="FG-PROBE-000003",
        triple_key=TripleKey(date(2026, 1, 15), "\u5c0f\u989d\u7ecf\u8425\u8d37", "\u4e8c\u7ea7\u5206\u884c"),
    )
    harness = Harness(case, clock=lambda: "2026-03-01T09:00:00")
    monitor = PostMonitorAgent(post_rule_map(), clock=lambda: "2026-03-01T09:00:00")
    flow = PostFlow(harness, monitor, VerifierAgent(clock=lambda: "2026-03-01T09:00:00"))

    # \u63a8\u8fdb\u5230 S6\uff08\u4e3b\u5e72\u8def\u5f84\uff09
    harness.transition(Stage.S1, actor="\u7f16\u6392\u5668")
    harness.transition(Stage.S2, actor="\u7f16\u6392\u5668")
    harness.transition(Stage.S3, actor="\u7f16\u6392\u5668")
    harness.transition(Stage.GATE_A, actor="\u7f16\u6392\u5668")
    harness.sign_gate("GATE-A", signer=Signer(
        account_id="p-a", display_name="\u63a2\u9488\u7b7e\u6838\u4eba",
        role="\u5185\u63a7\u5408\u89c4\u5ba1\u67e5\u5c97", authority_level=2,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="p-fp", ip="10.0.0.30"))
    harness.transition(Stage.S4, actor="\u7f16\u6392\u5668")
    harness.transition(Stage.GATE_B, actor="\u7f16\u6392\u5668")
    harness.sign_gate("GATE-B", signer=Signer(
        account_id="p-b", display_name="\u63a2\u9488\u7814\u5224\u4eba",
        role="\u98ce\u9669\u7814\u5224\u5c97", authority_level=3,
        authorized_case_types=("corp_credit_review",),
        device_fingerprint="p-fp2", ip="10.0.0.31"))
    harness.transition(Stage.S5, actor="\u7f16\u6392\u5668")
    harness.transition(Stage.S6, actor="\u7f16\u6392\u5668")

    view = PostCaseView(
        case_id="FG-PROBE-000003", product_type="\u5c0f\u989d\u7ecf\u8425\u8d37",
        org_level="\u4e8c\u7ea7\u5206\u884c", baseline_date="2026-01-15",
        rule_bindings=(("R-PM-001", "1.0.0"),),
        facts={"days_past_due": 45},
        field_meta={"days_past_due": {"channel": "api", "confidence": "\u9ad8"}},
    )
    flow.enter_monitoring(view)
    decoupled = not hasattr(view, "case") and not hasattr(view, "gates")
    rows.append(("FR-POST-01 \u5faa\u73af\u89e3\u8026", "\u8ddf\u8e2a\u89c6\u56fe\u4e0d\u542b\u4e3b\u6d41\u7a0b\u6848\u4ef6\u5f15\u7528", "\u89e3\u8026",
                 "\u5df2\u89e3\u8026" if decoupled else "\u672a\u89e3\u8026", decoupled))

    alerts = flow.scan("2026-03-01")
    lead_ok = bool(alerts) and alerts[0].lead_time_days == 45
    rows.append(("FR-POST-04 \u63d0\u524d\u91cf\u57fa\u51c6", "\u4ee5\u653e\u6b3e\u65e5\u4e3a\u57fa\u51c6\uff082026-01-15 \u2192 2026-03-01 = 45\u5929\uff09", "45\u5929",
                 "%d\u5929" % (alerts[0].lead_time_days if alerts else -1), lead_ok))

    flow.verify(alerts[0], {"days_past_due": 45})
    flow.submit_for_disposition(alerts[0])
    blocked = False
    try:
        harness.transition(Stage.S9, actor="\u7f16\u6392\u5668")
    except GateBypassError:
        blocked = True
    rows.append(("SEC-03 \u8d37\u540e\u7ed5\u884c", "\u672a\u7b7e\u6838\u76f4\u63a5\u843d\u5904\u7f6e\u5fc5\u987b\u88ab\u62e6", "0",
                 "\u5df2\u62e6\u622a" if blocked else "\u672a\u62e6\u622a", blocked))

    pending = harness.case.gates["GATE-C"].state.value == "PENDING"
    rows.append(("FR-POST-01 \u4e8b\u4ef6\u5904\u7f6e", "\u672a\u7b7e\u6838\u65f6\u8d37\u540e\u5904\u7f6e\u4e0d\u843d\u5730", "\u4e0d\u843d\u5730",
                 "\u4e0d\u843d\u5730" if (blocked and pending) else "\u843d\u5730", blocked and pending))

    ok = all(row[4] for row in rows)
    return rows, ok


def print_security_probe_rows(rows, ok, title="\u5b89\u5168\u63a2\u9488\u5b9e\u6d4b\uff08\u5207\u7247 5\uff1a\u4e09\u5904\u4eba\u5ba1\u95f8\u95e8\uff09") -> None:
    print()
    print(_c(title, BOLD, True))
    print("-" * 78)
    header = ("\u6307\u6807", "\u53e3\u5f84", "\u76ee\u6807", "\u5b9e\u6d4b", "\u7ed3\u8bba")
    print(_pad(header[0], 20) + _pad(header[1], 42) + _pad(header[2], 8)
          + _pad(header[3], 26) + header[4])
    for name, caliber, target, actual, passed in rows:
        verdict = _c("\u8fbe\u6807", GREEN, True) if passed else _c("\u4e0d\u8fbe\u6807", RED, True)
        print(_pad(name, 20) + _pad(caliber, 42) + _pad(target, 8)
              + _pad(actual, 26) + verdict)
    print("-" * 78)
    print(_c("\u63a2\u9488\u7ed3\u8bba\uff1a\u5168\u90e8\u62e6\u622a" if ok else "\u63a2\u9488\u7ed3\u8bba\uff1a\u5b58\u5728\u672a\u62e6\u622a\u7684\u7ed5\u8def\uff01",
             GREEN if ok else RED, True))


def main() -> int:
    _reconfigure_streams()
    ap = argparse.ArgumentParser(description="FinGuard 验证入口")
    ap.add_argument("--list", action="store_true", help="列出切片与套件状态")
    ap.add_argument("--slice", type=float, default=None, help="只跑指定切片的套件（支持 6.5 这类子切片）")
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

    # 计数口径（评审留档 A5）：总数与成功数按**测试方法**计；
    # subTest 失败会多次调用 addFailure，若按 failures 条数算会让「通过数」虚高，
    # 因此失败按**去重后的方法数**统计，并单列失败断言条数。
    total = result.testsRun
    failed_methods = {t for t, _ in result.failures}
    failed_methods |= {t for t, _ in result.errors}
    failed = len(failed_methods)
    failed_assertions = len(result.failures) + len(result.errors)
    passed = total - failed - len(result.skipped)

    print()
    print("-" * 78)
    skipped = len(result.skipped)
    if failed:
        print(_c("结果：%d / %d 通过，%d 个方法失败（%d 条失败断言）"
                 % (passed, total, failed, failed_assertions), RED, True))
    elif skipped:
        # A2：skipped 不计入 passed，措辞必须说清，不能笼统写"全部通过"
        print(_c("结果：%d / %d 通过（另有 %d 项跳过）" % (passed, total, skipped),
                 YELLOW, True))
    else:
        print(_c("结果：%d / %d 全部通过" % (passed, total), GREEN, True))

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
        probe_rows, probe_ok = run_harness_security_probe()
        print_security_probe_rows(probe_rows, probe_ok)
        if not probe_ok:
            print(_c("\u5b89\u5168\u63a2\u9488\u672a\u8fbe\u6807\uff1a\u4e0d\u5f97\u8fdb\u5165\u4e0b\u4e00\u68c0\u67e5\u70b9\u3002", RED, True))
            failed = failed or 1
        replay_rows, replay_ok = run_replay_probe()
        print_security_probe_rows(replay_rows, replay_ok, "\u56de\u653e\u63a2\u9488\u5b9e\u6d4b\uff08\u5207\u7247 6\uff1a\u4e8b\u4ef6\u65e5\u5fd7\u4e0e\u5377\u5b97\u56de\u653e\uff09")
        if not replay_ok:
            print(_c("\u56de\u653e\u63a2\u9488\u672a\u8fbe\u6807\uff1a\u4e0d\u5f97\u8fdb\u5165\u4e0b\u4e00\u68c0\u67e5\u70b9\u3002", RED, True))
            failed = failed or 1
        post_rows, post_ok = run_post_probe()
        print_security_probe_rows(post_rows, post_ok, "\u8d37\u540e\u63a2\u9488\u5b9e\u6d4b\uff08\u5207\u7247 7\uff1a\u5e38\u9a7b\u5faa\u73af\u3001\u63d0\u524d\u91cf\u4e0e GATE-C\uff09")
        if not post_ok:
            print(_c("\u8d37\u540e\u63a2\u9488\u672a\u8fbe\u6807\uff1a\u4e0d\u5f97\u8fdb\u5165\u4e0b\u4e00\u68c0\u67e5\u70b9\u3002", RED, True))
            failed = failed or 1

    print()
    print("检查点建议：改完任何一层都跑一次；切片的验收标准见 docs/ROADMAP.md。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
