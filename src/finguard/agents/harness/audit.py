"""判定链审计测量点（PRD 12.2 SEC-01 越权召回 = 0）。

评审（切片 3）明确要求：**在切片 5 落测量点**——因为 SEC-01 要统计
"进入判定链次数"，而"越权召回 = 0"必须能被**测量**，不能只是设计意图。

测量点挂在 FactSet.decision_ready() 上：它是判定链的**唯一入口**，
每次被调用即记录一条：

    · 谁调用了（caller）
    · 哪些字段进入了判定链（entered）
    · 哪些字段被拦住了、为什么（blocked + reason）

越权召回 = 「本应被拦的字段出现在 entered 里」的次数。这个值恒为 0，
但它是**算出来的**，不是写死的——判定链哪天改坏了，指标会立刻变成非 0。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = ["DecisionChainEntry", "DecisionChainAuditLog"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class DecisionChainEntry:
    """一次"进入判定链"的测量记录。"""

    seq: int
    case_id: str
    caller: str
    entered_fields: Tuple[str, ...]
    blocked_fields: Tuple[str, ...]
    blocked_reasons: Tuple[Tuple[str, str], ...]
    at: str

    @property
    def violations(self) -> Tuple[str, ...]:
        """本应被拦却进入了判定链的字段（越权召回）。正常恒为空。"""
        blocked = set(self.blocked_fields)
        return tuple(sorted(f for f in self.entered_fields if f in blocked))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "case_id": self.case_id,
            "caller": self.caller,
            "entered_fields": list(self.entered_fields),
            "blocked_fields": list(self.blocked_fields),
            "blocked_reasons": [list(r) for r in self.blocked_reasons],
            "violations": list(self.violations),
            "at": self.at,
        }


class DecisionChainAuditLog:
    """判定链进入审计日志（内存实现；切片 6 落不可篡改事件日志）。"""

    def __init__(self, *, clock: Optional[Callable[[], str]] = None) -> None:
        self._entries: List[DecisionChainEntry] = []
        self._seq = 0
        self._clock = clock or _now

    # ---------------- 挂载 ----------------
    def attach(self, factset: Any, *, caller: str, case_id: str) -> Any:
        """给事实集挂上审计钩子，返回**带钩子**的事实集副本。

        原事实集不被修改（FactSet 是不可变对象），因此同一个事实集
        可以被不同调用方分别测量。
        """
        log = self

        def hook(
            entered: Tuple[str, ...],
            blocked: Tuple[str, ...],
            reasons: Mapping[str, str],
        ) -> None:
            log._record(
                caller=caller,
                case_id=case_id,
                entered=tuple(entered),
                blocked=tuple(blocked),
                reasons=dict(reasons),
            )

        return replace(factset, decision_audit=hook)

    def _record(
        self,
        *,
        caller: str,
        case_id: str,
        entered: Tuple[str, ...],
        blocked: Tuple[str, ...],
        reasons: Mapping[str, str],
    ) -> DecisionChainEntry:
        self._seq += 1
        entry = DecisionChainEntry(
            seq=self._seq,
            case_id=case_id,
            caller=caller,
            entered_fields=tuple(sorted(entered)),
            blocked_fields=tuple(sorted(blocked)),
            blocked_reasons=tuple(sorted(reasons.items())),
            at=self._clock(),
        )
        self._entries.append(entry)
        return entry

    # ---------------- 查询 ----------------
    @property
    def entries(self) -> Tuple[DecisionChainEntry, ...]:
        return tuple(self._entries)

    @property
    def entered_count(self) -> int:
        """进入判定链的次数（PRD SEC-01 的统计口径之一）。"""
        return len(self._entries)

    @property
    def violation_count(self) -> int:
        """越权召回次数：被拦字段进入判定链的次数。目标值 0，无折中。"""
        return sum(len(e.violations) for e in self._entries)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entered_count": self.entered_count,
            "violation_count": self.violation_count,
            "entries": [e.to_dict() for e in self._entries],
        }