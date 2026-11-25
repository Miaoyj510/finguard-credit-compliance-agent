"""领域枚举：判定三态、严重度、覆盖度、未判定原因码。

全部取值与 PRD 保持一致，改动需同步 PRD：
- PRD 6.1 FR-RAG-01：判定返回 命中 / 不命中 / 不适用 三态
- PRD 6.3 FR-AUDIT-02：覆盖度 = 已判定 / 不适用 / 未判定
- PRD 6.3 FR-AUDIT-03：严重度由规则库定义，不自由生成
"""
from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """规则引擎返回的判定三态。"""

    HIT = "hit"                        # 命中
    MISS = "miss"                      # 未命中
    NOT_APPLICABLE = "not_applicable"  # 不适用


class Severity(str, Enum):
    """严重度分级（FR-AUDIT-03：只能来自规则库，模型不得自拟）。"""

    INFO = "提示"
    ATTENTION = "关注"
    MAJOR = "重要"
    CRITICAL = "重大"


class CoverageFlag(str, Enum):
    """覆盖度标记（FR-AUDIT-02）。"""

    JUDGED = "已判定"
    NOT_APPLICABLE = "不适用"
    UNRESOLVED = "未判定"


class UnresolvedReasonCode(str, Enum):
    """未判定原因码（FR-RAG-04 / FR-RAG-05）。

    这两个码是「须人工判定」的两种成因，也是 PRD 指标 Q-09 的统计口径：
    规则版本不唯一时，系统必须停下来问人，而不是猜。
    """

    NO_VERSION_MATCH = "NO_VERSION_MATCH"      # 三键下无任何生效版本
    AMBIGUOUS_VERSION = "AMBIGUOUS_VERSION"    # 三键下命中多个重叠版本
    MISSING_FACT = "MISSING_FACT"              # 判定所需事实缺失
    LOW_CONFIDENCE = "LOW_CONFIDENCE"          # 事实置信度不足（切片 3 起使用）
    DSL_PARSE_ERROR = "DSL_PARSE_ERROR"        # 表达式无法解析，规则降级为未判定
    TYPE_MISMATCH = "TYPE_MISMATCH"            # 事实类型不可比，降级为未判定
