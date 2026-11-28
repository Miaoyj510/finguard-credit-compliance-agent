"""编排期的案件文件（PRD 8.4 CaseState 结构）。

与 domain/case.py 的关系：

    · ``domain.case.CaseState`` —— **受理态骨架**（切片 1）：案件号 + 三键 + 绑定结果。
      它回答"这个案子按哪一版规则判"。
    · 本模块的 ``CaseFile`` —— **编排态全量状态**（切片 5）：PRD 8.4 的完整结构，
      回答"这个案子现在走到哪、谁签过、意见是什么、覆盖度是多少"。

两者不是重复建模：受理快照与当前状态在回放（切片 6）里必须能分开还原，
所以保留两个类型。``CaseFile.from_case_state`` 负责承接。

阶段字段**不可直接赋值**（PRD 8.5 约束：状态机不提供绕过边）。
`stage` 的 setter 只抛异常；唯一写入口是 ``_set_stage``，且必须带编排器令牌。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from ..agents.harness.gates import GateRecord, build_gates
from ..agents.harness.states import GateSignError, Stage, StageMutationError
from .case import CaseState, TripleKey

__all__ = ["CaseFile", "STAGE_WRITE_TOKEN"]


class _StageToken:
    """编排器写阶段用的内部令牌。

    ⚠ 这是**工程约束，不是安全边界**。同进程的代码可以拿到令牌；
    真正的边界是闸门签核记录 + 不可篡改事件日志（切片 6）。
    它的价值在于：让"绕过闸门"必须走一条**可被审计的**路径，
    而不是随手一行 `case.stage = ...`。
    """

    def __init__(self) -> None:
        self._issued = 0

    def issue(self) -> int:
        self._issued += 1
        return self._issued

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志
        return "<编排器阶段写令牌>"


#: 阶段写令牌（仅编排器持有）
STAGE_WRITE_TOKEN = _StageToken()


@dataclass
class CaseFile:
    """PRD 8.4 的 CaseState 全量结构。"""

    case_id: str
    triple_key: TripleKey
    case_type: str = "corp_credit_review"
    rule_binding: Tuple[Any, ...] = ()
    unresolved: Tuple[Any, ...] = ()
    facts_digest: Tuple[Any, ...] = ()
    opinions: Tuple[Any, ...] = ()
    coverage: Optional[Any] = None
    gates: Dict[str, GateRecord] = field(default_factory=build_gates)
    created_at: Optional[str] = None
    _stage: Stage = Stage.S0

    # ---------------- 阶段（只读 + 受控写） ----------------
    @property
    def stage(self) -> Stage:
        return self._stage

    @stage.setter
    def stage(self, value: Any) -> None:
        raise StageMutationError(
            "案件阶段不可直接赋值：只能由编排器按状态机迁移（当前 %s）" % self._stage.value
        )

    def set_stage(self, value: Any) -> None:
        """公开的"改阶段"入口——**一律拒绝**。

        保留这个方法是刻意的：让绕过尝试有明确的报错，而不是
        AttributeError 那种"看起来像程序错误"的失败。
        """
        raise StageMutationError(
            "案件阶段不可直接赋值：只能由编排器按状态机迁移（当前 %s）" % self._stage.value
        )

    def _set_stage(self, value: Stage, token: Any) -> None:
        """编排器专用写入口。token 不匹配一律拒绝。"""
        if token is not STAGE_WRITE_TOKEN:
            raise StageMutationError("阶段写入口只接受编排器令牌")
        self._stage = value

    # ---------------- 签核入口（一律拒绝） ----------------
    def sign(self, *args: Any, **kwargs: Any) -> None:
        """案件对象上的签核入口——**一律拒绝**。

        签核只能由 Harness.sign_gate() 驱动，并且要过闸门自身的
        资格校验（岗位 + 授权层级 + 案件类型 + 不可代签）。
        """
        raise GateSignError(
            "案件对象不接受直接签核：签核须经 Harness.sign_gate() 与闸门资格校验"
        )

    # ---------------- 构造 ----------------
    @classmethod
    def from_case_state(cls, case: CaseState, case_type: str = "corp_credit_review") -> "CaseFile":
        """由受理态骨架构造编排态案件文件。"""
        return cls(
            case_id=case.case_id,
            triple_key=case.triple_key,
            case_type=case_type,
            rule_binding=tuple(case.rule_binding),
            unresolved=tuple(case.unresolved),
            created_at=case.created_at,
        )

    # ---------------- 输出 ----------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "triple_key": self.triple_key.to_dict(),
            "case_type": self.case_type,
            "stage": self._stage.value,
            "rule_binding": [b.to_dict() for b in self.rule_binding],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "facts_digest": [dict(f) for f in self.facts_digest],
            "opinions": [o.to_dict() for o in self.opinions],
            "coverage": self.coverage.to_dict() if self.coverage is not None else None,
            "gates": {gid: g.to_dict() for gid, g in self.gates.items()},
            "created_at": self.created_at,
        }