"""切片 6 · 只追加事件日志与哈希链断言（PRD 8.9 不可篡改、11.3、NFR-09）。

本组断言的重点是**"改不动"**：日志只能追加、链能检出篡改、
对外没有任何修改 / 删除入口。
"""
from __future__ import annotations

import unittest

from finguard.storage.event_log import (
    GENESIS_HASH,
    ChainVerification,
    Event,
    EventLog,
    EventLogError,
    compute_hash,
)


def log_with_three() -> EventLog:
    log = EventLog(clock=lambda: "2026-11-26T10:00:00")
    log.append("FG-1", "stage_transition", {"to_stage": "S1"})
    log.append("FG-1", "opinion_generated", {"opinion_id": "OP-FG-1-001"})
    log.append("FG-2", "gate_signed", {"gate_id": "GATE-A", "actor": "u-001"})
    return log


class TestAppendOnly(unittest.TestCase):
    def test_追加后seq连续(self):
        log = log_with_three()
        self.assertEqual([e.seq for e in log.events()], [1, 2, 3])

    def test_链首前驱是创世哈希(self):
        log = log_with_three()
        self.assertEqual(log.events()[0].prev_hash, GENESIS_HASH)

    def test_后一条接住前一条的哈希(self):
        log = log_with_three()
        events = log.events()
        self.assertEqual(events[1].prev_hash, events[0].hash)
        self.assertEqual(events[2].prev_hash, events[1].hash)

    def test_缺case_id被拒(self):
        with self.assertRaises(EventLogError):
            EventLog().append("", "kind")

    def test_缺kind被拒(self):
        with self.assertRaises(EventLogError):
            EventLog().append("FG-1", "  ")

    def test_事件对象不可变(self):
        event = log_with_three().events()[0]
        with self.assertRaises(Exception):
            event.kind = "篡改"           # type: ignore[misc]

    def test_日志对外没有修改与删除入口(self):
        log = log_with_three()
        for name in ("delete", "remove", "update", "truncate", "pop", "clear", "set"):
            self.assertFalse(hasattr(log, name), "事件日志不应提供 %s" % name)

    def test_读取返回元组不可就地增删(self):
        events = log_with_three().events()
        self.assertIsInstance(events, tuple)
        self.assertEqual(len(events), 3)


class TestChainVerification(unittest.TestCase):
    def test_完整链校验通过(self):
        self.assertTrue(log_with_three().verify().ok)

    def test_校验结果含断裂定位(self):
        result = log_with_three().verify()
        self.assertIsNone(result.broken_seq)
        self.assertEqual(result.reason, "")

    def test_篡改内容被检出(self):
        log = log_with_three()
        events = list(log.events())
        # 白盒模拟篡改：事件对象不可变，但 payload 是可变字典
        events[1].payload["opinion_id"] = "OP-伪造"
        result = log.verify()
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_seq, 2)
        self.assertIn("内容", result.reason)

    def test_中间条目被删除被检出(self):
        log = log_with_three()
        log._events.pop(1)                     # 白盒模拟删除
        result = log.verify()
        self.assertFalse(result.ok)
        self.assertIn("seq", result.reason)

    def test_链首被替换被检出(self):
        log = log_with_three()
        log._events[0] = Event(
            seq=1, case_id="FG-1", kind="stage_transition", at="2026-11-26T10:00:00",
            payload={"to_stage": "S9"}, prev_hash=GENESIS_HASH, hash="deadbeef",
        )
        result = log.verify()
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_seq, 1)

    def test_单条自校验(self):
        event = log_with_three().events()[0]
        self.assertTrue(event.verify_self())
        self.assertFalse(
            compute_hash(event.seq, event.case_id, "别的", event.at, event.payload, event.prev_hash)
            == event.hash
        )


class TestQueries(unittest.TestCase):
    def test_按案件过滤(self):
        log = log_with_three()
        self.assertEqual(len(log.events("FG-1")), 2)
        self.assertEqual(len(log.events("FG-2")), 1)
        self.assertEqual(len(log.events("不存在的案")), 0)

    def test_since取起点及之后(self):
        log = log_with_three()
        self.assertEqual([e.seq for e in log.since("FG-1", 2)], [2])

    def test_跨案件共用一条全局链(self):
        """seq 与哈希链是**全局**的，不按案件断开——否则跨案拼接就检不出来了。"""
        log = log_with_three()
        self.assertEqual(log.events("FG-2")[0].prev_hash, log.events("FG-1")[1].hash)

    def test_sink适配器可接编排器事件(self):
        log = EventLog(clock=lambda: "2026-11-26T10:00:00")
        sink = log.append_sink()
        sink({"seq": 99, "at": "2026-11-26T10:00:00", "kind": "stage_transition",
              "case_id": "FG-1", "stage": "S1", "detail": {"to_stage": "S1"}})
        event = log.events()[0]
        self.assertEqual(event.seq, 1)          # 以日志自身 seq 为准
        self.assertEqual(event.payload["to_stage"], "S1")
        self.assertTrue(log.verify().ok)