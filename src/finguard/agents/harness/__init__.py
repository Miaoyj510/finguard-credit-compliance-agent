"""Harness 编排层（PRD 8.2 / 8.4 / 8.5 / 8.6 / 8.7）。

本包是"多 Agent Harness 编排运行时"的实现侧：

    states.py       17 状态与迁移白名单（含三处闸门的必经约束）
    gates.py        三处强制人审闸门（不可绕过 / 不可代签 / 超时只升级）
    handoff.py      Handoff 契约（PRD 8.6 必填字段，缺一即中断）
    orchestrator.py 编排器：驱动状态迁移、闸门签核与超时升级
    audit.py        判定链审计测量点（PRD SEC-01 越权召回 = 0）

⚠ 本包的 ``__init__`` 刻意**不做批量导入**：domain 层会引用本包的状态与
闸门模型，若这里在导入期把 orchestrator 拉进来，就会形成
domain ↔ agents 的循环导入。改为 PEP 562 惰性解析，按需取用。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "Harness",
    "Handoff",
    "Stage",
    "GateRecord",
    "GateState",
    "Signer",
    "DecisionChainAuditLog",
    "GateBypassError",
    "GateSignError",
    "HandoffIncompleteError",
    "StageMutationError",
    "TransitionError",
]

_LAZY = {
    "Harness": ".orchestrator",
    "Handoff": ".handoff",
    "Stage": ".states",
    "GateRecord": ".gates",
    "GateState": ".gates",
    "Signer": ".gates",
    "DecisionChainAuditLog": ".audit",
    "GateBypassError": ".states",
    "GateSignError": ".states",
    "HandoffIncompleteError": ".states",
    "StageMutationError": ".states",
    "TransitionError": ".states",
}


def __getattr__(name: str) -> Any:
    """PEP 562 惰性取用：只有真正访问某个名字时才导入对应模块。"""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from importlib import import_module
    module = import_module(module_name, __name__)
    return getattr(module, name)
