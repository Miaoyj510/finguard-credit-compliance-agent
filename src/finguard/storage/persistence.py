"""持久化：SQLite 仓库与事件日志的落库实现（切片 9.0）。

**为什么用 SQLite 而不是 postgres**：本机 16GB / 4C8T 的演示形态下，
SQLite（**标准库自带**、零安装、零外部依赖）足以支撑"案件 / 事件 / 卷宗 / 意见"
四类数据的持久化；切片 9.1 的生产形态再换 postgres——
所以本模块把**存储接口**收敛成一层，切片 6 的回放与卷宗逻辑**零改动**。

**接口稳定性**：``SqliteEventLog`` 与切片 6 的 ``EventLog`` 暴露**同样的方法**
（``append`` / ``events`` / ``since`` / ``verify``），回放引擎因此可以直接吃它——
这正是"依赖鸭子类型而不是具体类"的好处。

**哈希链跨进程**：链是**全局**的（seq 与 prev_hash 跨案件连续），
所以落库后仍能在库里整链重算校验——只追加的保证从内存搬到文件。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..storage.dossier import Dossier, DossierLayer, LayerSnapshot
from ..storage.event_log import (
    ChainVerification,
    Event,
    EventLogError,
    GENESIS_HASH,
    compute_hash,
)

__all__ = [
    "Repository",
    "SqliteEventLog",
    "SCHEMA",
    "utc_now",
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    case_type      TEXT NOT NULL,
    product_type   TEXT NOT NULL,
    org_level      TEXT NOT NULL,
    business_date  TEXT NOT NULL,
    stage          TEXT NOT NULL,
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY,
    case_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    at         TEXT NOT NULL,
    payload    TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id, seq);

CREATE TABLE IF NOT EXISTS dossier_layers (
    case_id      TEXT NOT NULL,
    layer        TEXT NOT NULL,
    payload      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    written_at   TEXT NOT NULL,
    PRIMARY KEY (case_id, layer)
);

CREATE TABLE IF NOT EXISTS opinions (
    case_id     TEXT NOT NULL,
    opinion_id  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    written_at  TEXT NOT NULL,
    PRIMARY KEY (case_id, opinion_id)
);
"""


def utc_now() -> str:
    """统一的时间戳口径：**naive UTC**（与切片 6 评审 C2 的决定一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class Repository:
    """SQLite 仓库（案件 / 事件 / 卷宗层 / 意见）。"""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        # WAL + 外键：并发读的可见性与引用完整性
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---------------- 案件 ----------------
    def save_case(
        self,
        case_id: str,
        *,
        case_type: str,
        product_type: str,
        org_level: str,
        business_date: str,
        stage: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        now = utc_now()
        body = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)
        with self._conn:
            self._conn.execute(
                "INSERT INTO cases (case_id, case_type, product_type, org_level,"
                " business_date, stage, payload, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(case_id) DO UPDATE SET"
                " case_type=excluded.case_type, product_type=excluded.product_type,"
                " org_level=excluded.org_level, business_date=excluded.business_date,"
                " stage=excluded.stage, payload=excluded.payload,"
                " updated_at=excluded.updated_at",
                (case_id, case_type, product_type, org_level, business_date,
                 stage, body, now, now),
            )

    def load_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return data

    def list_cases(self) -> Tuple[Dict[str, Any], ...]:
        rows = self._conn.execute(
            "SELECT * FROM cases ORDER BY business_date DESC, case_id").fetchall()
        return tuple(dict(r) for r in rows)

    def update_stage(self, case_id: str, stage: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE cases SET stage = ?, updated_at = ? WHERE case_id = ?",
                (stage, utc_now(), case_id),
            )

    # ---------------- 事件 ----------------
    def append_event(
        self,
        case_id: str,
        kind: str,
        payload: Mapping[str, Any],
        at: str,
        prev_hash: str,
        digest: str,
        seq: int,
    ) -> Event:
        body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (seq, case_id, kind, at, payload, prev_hash, hash)"
                " VALUES (?,?,?,?,?,?,?)",
                (seq, case_id, kind, at, body, prev_hash, digest),
            )
        return Event(seq=seq, case_id=case_id, kind=kind, at=at,
                    payload=dict(payload), prev_hash=prev_hash, hash=digest)

    def next_seq(self) -> int:
        row = self._conn.execute("SELECT MAX(seq) AS m FROM events").fetchone()
        return 1 if row is None or row["m"] is None else int(row["m"]) + 1

    def last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return GENESIS_HASH if row is None else row["hash"]

    def load_events(self, case_id: Optional[str] = None) -> Tuple[Event, ...]:
        if case_id is None:
            rows = self._conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE case_id = ? ORDER BY seq", (case_id,)).fetchall()
        return tuple(
            Event(seq=r["seq"], case_id=r["case_id"], kind=r["kind"], at=r["at"],
                  payload=json.loads(r["payload"]), prev_hash=r["prev_hash"],
                  hash=r["hash"])
            for r in rows
        )

    # ---------------- 卷宗 ----------------
    def save_dossier_layer(self, case_id: str, snapshot: LayerSnapshot) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO dossier_layers (case_id, layer, payload, content_hash, written_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(case_id, layer) DO UPDATE SET"
                " payload=excluded.payload, content_hash=excluded.content_hash,"
                " written_at=excluded.written_at",
                (case_id, snapshot.layer.value,
                 json.dumps(dict(snapshot.payload), ensure_ascii=False, sort_keys=True),
                 snapshot.content_hash, snapshot.written_at),
            )

    def load_dossier(self, case_id: str) -> Dossier:
        dossier = Dossier(case_id)
        rows = self._conn.execute(
            "SELECT * FROM dossier_layers WHERE case_id = ? ORDER BY layer",
            (case_id,)).fetchall()
        for row in rows:
            layer = DossierLayer(row["layer"])
            dossier._layers[layer] = LayerSnapshot(
                layer=layer,
                payload=json.loads(row["payload"]),
                content_hash=row["content_hash"],
                written_at=row["written_at"],
            )
        return dossier

    # ---------------- 意见 ----------------
    def save_opinion(self, case_id: str, opinion: Any) -> None:
        payload = opinion.to_dict() if hasattr(opinion, "to_dict") else dict(opinion)
        with self._conn:
            self._conn.execute(
                "INSERT INTO opinions (case_id, opinion_id, payload, written_at)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(case_id, opinion_id) DO UPDATE SET"
                " payload=excluded.payload, written_at=excluded.written_at",
                (case_id, payload.get("opinion_id", ""),
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), utc_now()),
            )

    def load_opinions(self, case_id: str) -> Tuple[Dict[str, Any], ...]:
        rows = self._conn.execute(
            "SELECT payload FROM opinions WHERE case_id = ? ORDER BY opinion_id",
            (case_id,)).fetchall()
        return tuple(json.loads(r["payload"]) for r in rows)

    def close(self) -> None:
        self._conn.close()


class SqliteEventLog:
    """落库版事件日志——与切片 6 的 ``EventLog`` **同接口**。

    回放引擎依赖的是鸭子类型（``events`` / ``since`` / ``verify``），
    因此把它换成本实现，**切片 6 的回放逻辑一行都不用改**——
    这是当初把接口收到那么窄的原因。

    只追加的保证从"内存里 append_only"变成"数据库里只有 INSERT"，
    哈希链也照样能在库里整链重算。
    """

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    # ---------------- 追加 ----------------
    def append(
        self,
        case_id: str,
        kind: str,
        payload: Optional[Mapping[str, Any]] = None,
        at: Optional[str] = None,
    ) -> Event:
        if not isinstance(case_id, str) or not case_id.strip():
            raise EventLogError("事件缺少 case_id")
        if not isinstance(kind, str) or not kind.strip():
            raise EventLogError("事件缺少 kind")
        body = dict(payload or {})
        moment = at or utc_now()
        seq = self._repo.next_seq()
        prev_hash = self._repo.last_hash()
        digest = compute_hash(seq, case_id, kind, moment, body, prev_hash)
        return self._repo.append_event(case_id, kind, body, moment, prev_hash, digest, seq)

    # ---------------- 读取 ----------------
    def events(self, case_id: Optional[str] = None) -> Tuple[Event, ...]:
        return self._repo.load_events(case_id)

    def since(self, case_id: str, seq: int) -> Tuple[Event, ...]:
        return tuple(e for e in self._repo.load_events(case_id) if e.seq >= seq)

    def last_hash(self) -> str:
        return self._repo.last_hash()

    def __len__(self) -> int:
        return len(self._repo.load_events())

    # ---------------- 校验 ----------------
    def verify(self) -> ChainVerification:
        """整链重算（与切片 6 的 ``EventLog.verify`` 同口径）。"""
        prev = GENESIS_HASH
        for index, event in enumerate(self._repo.load_events()):
            if event.seq != index + 1:
                return ChainVerification(
                    False, event.seq,
                    "seq 不连续：期望 %d，实际 %d" % (index + 1, event.seq))
            if event.prev_hash != prev:
                return ChainVerification(False, event.seq, "prev_hash 接不上：链在此处断裂")
            if not event.verify_self():
                return ChainVerification(False, event.seq, "条目内容与哈希不符（内容被改动过）")
            prev = event.hash
        return ChainVerification(True, None, "")
