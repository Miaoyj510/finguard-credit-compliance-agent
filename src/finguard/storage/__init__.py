"""持久化与卷宗层（切片 6）。

    event_log.py   只追加事件日志 + 哈希链（PRD 8.9 不可篡改、NFR-09）
    dossier.py     案件卷宗六层 + 两档齐全性校验（PRD 8.9、FR-GATE-05）

⚠ 本包 **不做批量导入**，避免与 domain / agents 形成循环导入。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "Event",
    "EventLog",
    "ChainVerification",
    "EventLogError",
    "Dossier",
    "DossierLayer",
    "Completeness",
    "LayerSnapshot",
    "DossierVerification",
    "content_hash",
]

_LAZY = {
    "Event": ".event_log",
    "EventLog": ".event_log",
    "ChainVerification": ".event_log",
    "EventLogError": ".event_log",
    "Dossier": ".dossier",
    "DossierLayer": ".dossier",
    "Completeness": ".dossier",
    "LayerSnapshot": ".dossier",
    "DossierVerification": ".dossier",
    "content_hash": ".dossier",
}


def __getattr__(name: str) -> Any:
    from importlib import import_module
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = import_module(module_name, __name__)
    return getattr(module, name)