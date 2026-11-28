"""知识层（切片 6.5）。

    guard.py     外部语料**入厂闸门**：结论词与夹带指令的检测与隔离标记
    taxonomy.py  GB/T 4754 行业映射与「只校准相对量」的口径定义

本包与 `finguard.facts` / `finguard.agents` 之间没有反向依赖，可安全导入。
"""
from __future__ import annotations

from .guard import (
    IsolationTag,
    ScreenResult,
    isolation_report,
    screen_corpus,
    screen_text,
)
from .taxonomy import (
    GBT4754,
    SECTOR_MAP,
    RELATIVE_ONLY_NOTE,
    TERMINAL_BUCKETS,
    TERMINAL_BUCKET_LABELS,
    SectorMapping,
    bucket_tenor,
    map_sector,
)

__all__ = [
    "IsolationTag",
    "ScreenResult",
    "isolation_report",
    "screen_corpus",
    "screen_text",
    "GBT4754",
    "SECTOR_MAP",
    "RELATIVE_ONLY_NOTE",
    "TERMINAL_BUCKETS",
    "TERMINAL_BUCKET_LABELS",
    "SectorMapping",
    "bucket_tenor",
    "map_sector",
]