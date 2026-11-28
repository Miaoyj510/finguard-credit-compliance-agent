"""只追加事件日志与哈希链（PRD 8.9 不可篡改、11.3 留痕与审计、NFR-09）。

三条不可让步的规则：

1. **只追加**：对外只暴露 ``append`` / 读接口，没有修改与删除入口；
   事件对象本身不可变（frozen）。
2. **哈希链**：每条事件带 ``prev_hash``，``hash = sha256(prev_hash | 规范化的条目内容)``，
   链断即可检出篡改。PRD 11.4 S-04（事实投毒）与 S-09（伪造历史签核记录）
   都依赖这条链。
3. **不补写、不伪造**：``append`` 强制 seq 连续；F-13 的处置原则是
   「已写入条目保留，禁止补写或伪造条目」，因此断链只能报出来，不能补平。

本切片是**进程内**实现。生产形态下同一份语义由数据库表 + 只追加约束承载
（PRD 7.7.2 / F-10），本切片负责把语义与校验跑通。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "Event",
    "EventLog",
    "ChainVerification",
    "EventLogError",
    "GENESIS_HASH",
    "compute_hash",
]

#: 创世哈希：链首事件的前驱
GENESIS_HASH = "0" * 64


class EventLogError(Exception):
    """事件日志异常（追加非法、链不连续等）。"""


def _canonical(payload: Mapping[str, Any]) -> str:
    """规范化 JSON：键排序、无多余空格、保留中文。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_hash(
    seq: int,
    case_id: str,
    kind: str,
    at: str,
    payload: Mapping[str, Any],
    prev_hash: str,
) -> str:
    """计算条目哈希。参与计算的内容与 prev_hash 一起进摘要。"""
    body = _canonical(
        {
            "seq": seq,
            "case_id": case_id,
            "kind": kind,
            "at": at,
            "payload": payload,
        }
    )
    return hashlib.sha256(("%s|%s" % (prev_hash, body)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Event:
    """一条只追加事件。字段全部不可变。"""

    seq: int
    case_id: str
    kind: str
    at: str
    payload: Mapping[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "case_id": self.case_id,
            "kind": self.kind,
            "at": self.at,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    def verify_self(self) -> bool:
        """重算本条哈希，确认内容未被改动。"""
        return compute_hash(
            self.seq, self.case_id, self.kind, self.at, self.payload, self.prev_hash
        ) == self.hash


@dataclass(frozen=True)
class ChainVerification:
    """哈希链校验结果。"""

    ok: bool
    broken_seq: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "broken_seq": self.broken_seq,
            "reason": self.reason,
        }


class EventLog:
    """只追加事件日志。

    对外接口只有三类：追加、读取、校验。**没有 update / delete / truncate**。
    """

    def __init__(self, *, clock: Optional[Callable[[], str]] = None) -> None:
        self._events: List[Event] = []
        self._by_case: Dict[str, List[Event]] = {}
        self._clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))

    # ---------------- 追加 ----------------
    def append(
        self,
        case_id: str,
        kind: str,
        payload: Optional[Mapping[str, Any]] = None,
        at: Optional[str] = None,
    ) -> Event:
        """追加一条事件。seq 自动连续，不接受外部指定。

        payload **深拷贝入库**（评审 RP5）：调用方在 append 之后复用或改动
        自己传入的嵌套 dict，不应把一条合法事件变成「被篡改」——留痕与调用方
        内存必须隔离。同时不可 JSON 序列化的 payload 归入 EventLogError，
        不让 TypeError 裸奔（调用方能拿到统一口径的失败原因）。
        """
        if not isinstance(case_id, str) or not case_id.strip():
            raise EventLogError("事件缺少 case_id")
        if not isinstance(kind, str) or not kind.strip():
            raise EventLogError("事件缺少 kind")
        if payload is not None and not isinstance(payload, Mapping):
            raise EventLogError("事件 payload 须为映射，收到 %r" % type(payload).__name__)
        body = copy.deepcopy(dict(payload or {}))
        seq = len(self._events) + 1
        prev_hash = self._events[-1].hash if self._events else GENESIS_HASH
        moment = at or self._clock()
        try:
            digest = compute_hash(seq, case_id, kind, moment, body, prev_hash)
        except (TypeError, ValueError) as exc:
            raise EventLogError("事件 payload 不可 JSON 序列化：%s" % exc)
        event = Event(
            seq=seq,
            case_id=case_id,
            kind=kind,
            at=moment,
            payload=body,
            prev_hash=prev_hash,
            hash=digest,
        )
        self._events.append(event)
        self._by_case.setdefault(case_id, []).append(event)
        return event

    def append_sink(self) -> Callable[[Mapping[str, Any]], None]:
        """返回一个可直接当 Harness 的 ``event_sink`` 用的回调。

        Harness 产出的是 dict（含 seq / at / kind / case_id / stage / detail），
        这里转成标准事件条目。注意：**日志的 seq 以日志自身为准**，
        不采信调用方给的 seq。
        """

        def sink(event: Mapping[str, Any]) -> None:
            detail = event.get("detail")
            payload = dict(detail) if isinstance(detail, Mapping) else {}
            stage = event.get("stage")
            if stage and "stage" not in payload:
                payload["stage"] = stage
            self.append(
                event.get("case_id", ""),
                event.get("kind", ""),
                payload,
                at=event.get("at"),
            )

        return sink

    # ---------------- 读取 ----------------
    def events(self, case_id: Optional[str] = None) -> Tuple[Event, ...]:
        """读取事件。给 case_id 则只返回该案事件。返回**元组**（不可就地改）。"""
        if case_id is None:
            return tuple(self._events)
        return tuple(self._by_case.get(case_id, ()))

    def since(self, case_id: str, seq: int) -> Tuple[Event, ...]:
        """取该案 seq **及之后**的事件（回放起点用）。"""
        return tuple(e for e in self._by_case.get(case_id, ()) if e.seq >= seq)

    def last_hash(self) -> str:
        return self._events[-1].hash if self._events else GENESIS_HASH

    def __len__(self) -> int:
        return len(self._events)

    # ---------------- 校验 ----------------
    def verify(self) -> ChainVerification:
        """校验整条哈希链。

        检出三类问题：seq 不连续、prev_hash 接不上、条目内容被改过。
        """
        prev = GENESIS_HASH
        for index, event in enumerate(self._events):
            if event.seq != index + 1:
                return ChainVerification(
                    False, event.seq, "seq 不连续：期望 %d，实际 %d" % (index + 1, event.seq)
                )
            if event.prev_hash != prev:
                return ChainVerification(
                    False, event.seq, "prev_hash 接不上：链在此处断裂"
                )
            if not event.verify_self():
                return ChainVerification(
                    False, event.seq, "条目内容与哈希不符（内容被改动过）"
                )
            prev = event.hash
        return ChainVerification(True, None, "")