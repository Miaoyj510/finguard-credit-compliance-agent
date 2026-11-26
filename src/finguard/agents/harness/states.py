"""编排状态机（PRD 8.2 图 8-1、8.5 状态清单）。

本模块只描述**状态与合法迁移**，不含任何业务动作。业务动作在 orchestrator.py。

三条不可让步的规则：

1. **17 个状态全部可达**：从 S0 出发能走到每一个状态（含三处闸门）。
2. **闸门是必经节点**：状态机**不提供绕过边**——S3 到 S4 必须经过 GATE_A，
   S4 到 S5 必须经过 GATE_B，S8 到 S9 必须经过 GATE_C。
3. **闸门的出边不参与自动推进**：闸门节点的出边只能由签核 / 退回驱动，
   编排器不能自己穿过去。

关于"防绕过"的强度说明：状态字段（CaseFile.stage）**不可直接赋值**，
写入口只有编排器持有的内部令牌。这是一道**工程约束**，不是安全边界——
真正的安全边界是「闸门签核记录 + 不可篡改事件日志」（切片 6），
因为任何进程内约束都能被同进程的代码绕开，审计必须靠留痕而不是靠内存。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

__all__ = [
    "Stage",
    "STAGE_LABELS",
    "GATE_STAGES",
    "GATE_ENTRY",
    "GATE_RELEASE",
    "TRANSITIONS",
    "MAIN_PATH",
    "HarnessError",
    "TransitionError",
    "GateBypassError",
    "StageMutationError",
    "GateSignError",
    "HandoffIncompleteError",
    "is_gate",
    "allowed_next",
    "gate_of_release",
    "can_reach",
    "bypass_edges",
    "reachable_states",
]


class Stage(str, Enum):
    """17 个案件状态（PRD 8.5 状态清单，顺序与图 8-1 一致）。"""

    S0 = "S0"                              # 受理
    S1 = "S1"                              # 事实抽取
    S1_MANUAL_CONFLICT = "S1_MANUAL_CONFLICT"  # 冲突裁决等待
    S2 = "S2"                              # 规则绑定与判定
    S2_UNRESOLVED = "S2_UNRESOLVED"        # 须人工判定池
    S3 = "S3"                              # 审查意见成稿
    GATE_A = "GATE_A"                      # 审查意见签核
    S4 = "S4"                              # 风险研判
    GATE_B = "GATE_B"                      # 处置建议签核
    S5 = "S5"                              # 卷宗组装
    S6 = "S6"                              # 已归档（带签核记录）
    POST_LOOP = "POST_LOOP"                # 贷后监测中（常驻循环）
    S7 = "S7"                              # 预警生成
    S8 = "S8"                              # 核查初核
    GATE_C = "GATE_C"                      # 预警处置签核
    S9 = "S9"                              # 贷后处置归档
    S10 = "S10"                            # 可回放归档


#: 状态中文名（界���与卷宗用）
STAGE_LABELS: Mapping[Stage, str] = {
    Stage.S0: "受理",
    Stage.S1: "事实抽取",
    Stage.S1_MANUAL_CONFLICT: "冲突裁决等待",
    Stage.S2: "规则绑定与判定",
    Stage.S2_UNRESOLVED: "须人工判定池",
    Stage.S3: "审查意见成稿",
    Stage.GATE_A: "审查意见签核",
    Stage.S4: "风险研判",
    Stage.GATE_B: "处置建议签核",
    Stage.S5: "卷宗组装",
    Stage.S6: "已归档（带签核记录）",
    Stage.POST_LOOP: "贷后监测中",
    Stage.S7: "预警生成",
    Stage.S8: "核查初核",
    Stage.GATE_C: "预警处置签核",
    Stage.S9: "贷后处置归档",
    Stage.S10: "可回放归档",
}

#: 三处强制人审闸门（PRD 6.5 FR-GATE-01、8.7）
GATE_STAGES: Tuple[Stage, ...] = (Stage.GATE_A, Stage.GATE_B, Stage.GATE_C)

#: 闸门的**进入前置状态**：闸门只能从它这里进（也用于退回时的落点）
GATE_ENTRY: Mapping[Stage, Stage] = {
    Stage.GATE_A: Stage.S3,
    Stage.GATE_B: Stage.S4,
    Stage.GATE_C: Stage.S8,
}

#: 闸门的**放行后继状态**：进入它必须已签核（FR-GATE-01 绕行尝试数 = 0）
GATE_RELEASE: Mapping[Stage, Stage] = {
    Stage.GATE_A: Stage.S4,
    Stage.GATE_B: Stage.S5,
    Stage.GATE_C: Stage.S9,
}

#: 状态转移表（PRD 8.2 图 8-1 + 8.5 状态清单）
#:
#: 注意三条闸门边的形状：
#:   S3  -> GATE_A        （只能进闸门，不能直接到 S4）
#:   GATE_A -> {S4, S3}   （S4 = 签核通过放行，S3 = 退回修改）
TRANSITIONS: Mapping[Stage, FrozenSet[Stage]] = {
    Stage.S0: frozenset({Stage.S1}),
    Stage.S1: frozenset({Stage.S2, Stage.S1_MANUAL_CONFLICT}),
    Stage.S1_MANUAL_CONFLICT: frozenset({Stage.S1}),
    Stage.S2: frozenset({Stage.S3, Stage.S2_UNRESOLVED}),
    Stage.S2_UNRESOLVED: frozenset({Stage.S3}),
    Stage.S3: frozenset({Stage.GATE_A}),
    Stage.GATE_A: frozenset({Stage.S4, Stage.S3}),
    Stage.S4: frozenset({Stage.GATE_B}),
    Stage.GATE_B: frozenset({Stage.S5, Stage.S4}),
    Stage.S5: frozenset({Stage.S6}),
    Stage.S6: frozenset({Stage.POST_LOOP}),
    Stage.POST_LOOP: frozenset({Stage.S7, Stage.S6}),
    Stage.S7: frozenset({Stage.S8}),
    Stage.S8: frozenset({Stage.GATE_C}),
    Stage.GATE_C: frozenset({Stage.S9, Stage.S8}),
    Stage.S9: frozenset({Stage.S10}),
    Stage.S10: frozenset(),
}

#: 主干路径（自动推进演示用）：从 S0 到 GATE_A 的顺序
MAIN_PATH: Tuple[Stage, ...] = (
    Stage.S0, Stage.S1, Stage.S2, Stage.S3, Stage.GATE_A,
    Stage.S4, Stage.GATE_B, Stage.S5, Stage.S6, Stage.POST_LOOP,
    Stage.S7, Stage.S8, Stage.GATE_C, Stage.S9, Stage.S10,
)


# ---------------------------------------------------------------- 异常
class HarnessError(Exception):
    """Harness 层异常基类。"""


class TransitionError(HarnessError):
    """非法状态迁移。"""


class GateBypassError(TransitionError):
    """试图绕过人审闸门（SEC-03 闸门绕行 = 0）。"""


class StageMutationError(HarnessError):
    """试图直接改写案件阶段字段。"""


class GateSignError(HarnessError):
    """签核动作被拒（无资格 / 代签 / 重复签核 / 状态不对）。"""


class HandoffIncompleteError(HarnessError):
    """Handoff 必填字段缺失，交接中断。"""


# ---------------------------------------------------------------- 查询
def is_gate(stage: Stage) -> bool:
    """是否是闸门状态。"""
    return stage in GATE_STAGES


def allowed_next(stage: Stage) -> FrozenSet[Stage]:
    """某状态允许的下一状态集合。"""
    return TRANSITIONS.get(stage, frozenset())


def gate_of_release(stage: Stage) -> Optional[Stage]:
    """若进入 stage 必须先过闸门，返回该闸门；否则返回 None。"""
    for gate, release in GATE_RELEASE.items():
        if release is stage:
            return gate
    return None


def reachable_states(start: Stage = Stage.S0) -> Tuple[Stage, ...]:
    """从 start 出发可达的全部状态（广度优先）。"""
    seen = {start}
    queue: List[Stage] = [start]
    while queue:
        current = queue.pop(0)
        for nxt in sorted(allowed_next(current), key=lambda s: s.value):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return tuple(sorted(seen, key=lambda s: s.value))


def can_reach(target: Stage, start: Stage = Stage.S0) -> bool:
    """target 是否从 start 可达。"""
    return target in reachable_states(start)


def bypass_edges() -> Tuple[Tuple[Stage, Stage], ...]:
    """返回所有**绕过闸门**的非法边（正常应为空）。

    判定口径：对每个闸门放行后继状态 R(g)，它的合法前驱只有
        · 闸门 g 本身（签核通过放行），以及
        · 下一个闸门（退回修改时落回 R(g)，例如 GATE_B 退回即回到 S4）。
    任何**其它**前驱都是绕行边（例如 S3 -> S4 直连）。
    """
    bad: List[Tuple[Stage, Stage]] = []
    for gate, release in GATE_RELEASE.items():
        allowed_predecessors = {gate}
        for other, entry in GATE_ENTRY.items():
            if entry is release:
                allowed_predecessors.add(other)
        for src, dsts in TRANSITIONS.items():
            if release in dsts and src not in allowed_predecessors:
                bad.append((src, release))
    return tuple(bad)