"""切片 5 · 状态机断言（PRD 8.2 图 8-1、8.5 状态清单）。

本组断言的重点不是"流程能走通"，而是**结构上不存在绕行边**。
"""
from __future__ import annotations

import unittest

from finguard.agents.harness.states import (
    GATE_ENTRY,
    GATE_RELEASE,
    GATE_STAGES,
    STAGE_LABELS,
    TRANSITIONS,
    Stage,
    bypass_edges,
    can_reach,
    is_gate,
    reachable_states,
)

#: PRD 8.5 的 17 个状态（人工抄录，用于防漂移）
PRD_STATES = (
    "S0", "S1", "S1_MANUAL_CONFLICT", "S2", "S2_UNRESOLVED", "S3", "GATE_A",
    "S4", "GATE_B", "S5", "S6", "POST_LOOP", "S7", "S8", "GATE_C", "S9", "S10",
)


class TestStateInventory(unittest.TestCase):
    def test_状态数与PRD一致(self):
        self.assertEqual(len(list(Stage)), 17)
        self.assertEqual(tuple(s.value for s in Stage), PRD_STATES)

    def test_三处闸门齐备(self):
        self.assertEqual(len(GATE_STAGES), 3)
        self.assertEqual(
            tuple(g.value for g in GATE_STAGES), ("GATE_A", "GATE_B", "GATE_C")
        )

    def test_全部状态从S0可达(self):
        for target in Stage:
            self.assertTrue(can_reach(target), "%s 不可达" % target.value)

    def test_可达状态数等于状态总数(self):
        self.assertEqual(len(reachable_states(Stage.S0)), 17)

    def test_不存在绕行边(self):
        """放行后继状态只能由它自己的闸门（以及下一个闸门的退回）进入。"""
        self.assertEqual(bypass_edges(), ())

    def test_闸门出边只有放行与退回两个落点(self):
        for gate in GATE_STAGES:
            outs = TRANSITIONS[gate]
            self.assertEqual(outs, frozenset({GATE_RELEASE[gate], GATE_ENTRY[gate]}))

    def test_闸门前置状态进不了放行后继状态(self):
        """S3 不能直接到 S4、S4 不能直接到 S5、S8 不能直接到 S9。"""
        self.assertNotIn(Stage.S4, TRANSITIONS[Stage.S3])
        self.assertNotIn(Stage.S5, TRANSITIONS[Stage.S4])
        self.assertNotIn(Stage.S9, TRANSITIONS[Stage.S8])

    def test_状态标签齐全(self):
        for stage in Stage:
            self.assertTrue(STAGE_LABELS[stage])
        self.assertTrue(is_gate(Stage.GATE_A))
        self.assertFalse(is_gate(Stage.S3))