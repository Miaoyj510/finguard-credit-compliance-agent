"""时间戳口径统一（切片 6 评审咨询 C2）。

**为什么要有这个模块**：事件日志的 ``at`` 与闸门的 ``signed_at`` 是**同一个事实的两种写法**，
如果一个存时钟原串、另一个存归一化值，那么：

    · naive 时钟下两者恒等（看不出问题）
    · **带时区时钟下两者会失配**——签核对账判不一致，回放报"当时与现在对不上"

这类缺陷在本地演示时不会暴露，一旦接了真实环境（带时区）就炸。
所以把口径收敛到**一处**：所有留痕时间戳一律归一为 **naive UTC**。

⚠ 口径变更会影响既有事件的可比性：归一是**幂等**的（已归一的再归一不变），
所以历史事件不需要回填。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["normalize_timestamp", "TimestampError"]


class TimestampError(ValueError):
    """时间戳无法解析或归一。"""


def normalize_timestamp(text: Any, what: str = "时间戳") -> str:
    """把 ISO 8601 时间串归一为 **naive UTC** 的 ISO 串。

    · 非字符串 / 不可解析 → TimestampError
    · 带时区 → 归一到 UTC 后去掉时区标记
    · 已是 naive UTC → 原样（幂等）
    """
    if not isinstance(text, str):
        raise TimestampError("%s须为 ISO 8601 字符串，收到 %r" % (what, text))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise TimestampError("%s无法解析（须为 ISO 8601）：%r" % (what, text))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat()