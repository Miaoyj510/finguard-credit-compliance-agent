"""冲突裁决队列。

PRD 6.2 FR-FACT-06：冲突项**标红并列两个取值**，提交人工裁决；
8.2：裁决 = 人工选择 + **必填理由**。

两条硬约束：
    1. 裁决必须带理由——空理由直接拒绝（否则事后无法复盘为什么这么判）。
    2. 裁决值必须来自候选列表——不允许凭空造一个值。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .models import Confidence, FactField, SourceChannel

__all__ = ["ConflictKind", "ConflictStatus", "ConflictItem", "ConflictQueue"]


class ConflictKind(str, Enum):
    LOW_CONFIDENCE = "low_confidence"   # 双路抽取不一致 / 仅一路抽到
    CROSS_CHECK = "cross_check"         # 交叉一致性核对不通过


class ConflictStatus(str, Enum):
    PENDING = "待裁决"
    RESOLVED = "已裁决"


@dataclass(frozen=True)
class ConflictItem:
    """一条待裁决（或已裁决）的冲突项。"""

    conflict_id: str
    field: str
    kind: ConflictKind
    candidates: Tuple[Any, ...]
    detail: str
    status: ConflictStatus = ConflictStatus.PENDING
    chosen: Optional[Any] = None
    reason: Optional[str] = None
    adjudicator: Optional[str] = None
    adjudicated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "field": self.field,
            "kind": self.kind.value,
            "candidates": list(self.candidates),
            "detail": self.detail,
            "status": self.status.value,
            "chosen": self.chosen,
            "reason": self.reason,
            "adjudicator": self.adjudicator,
            "adjudicated_at": self.adjudicated_at,
        }


class ConflictQueue:
    """冲突项队列。裁决后回填为高置信事实。"""

    def __init__(self, items: Iterable[ConflictItem] = ()) -> None:
        self._items: Dict[str, ConflictItem] = {}
        for item in items:
            self._items[item.conflict_id] = item

    # ---------- 读 ----------
    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> Tuple[ConflictItem, ...]:
        return tuple(self._items[k] for k in sorted(self._items))

    def pending(self) -> Tuple[ConflictItem, ...]:
        return tuple(i for i in self.all() if i.status is ConflictStatus.PENDING)

    def resolved(self) -> Tuple[ConflictItem, ...]:
        return tuple(i for i in self.all() if i.status is ConflictStatus.RESOLVED)

    def get(self, conflict_id: str) -> ConflictItem:
        if conflict_id not in self._items:
            raise KeyError("冲突项不存在：%s" % conflict_id)
        return self._items[conflict_id]

    # ---------- 写 ----------
    def add(self, item: ConflictItem) -> ConflictItem:
        if item.conflict_id in self._items:
            raise ValueError("冲突项重复：%s" % item.conflict_id)
        self._items[item.conflict_id] = item
        return item

    def resolve(
        self,
        conflict_id: str,
        chosen: Any,
        reason: str,
        adjudicator: str,
        adjudicated_at: Optional[str] = None,
    ) -> ConflictItem:
        """人工裁决。理由必填；chosen 必须在候选列表内。"""
        item = self.get(conflict_id)
        if item.status is ConflictStatus.RESOLVED:
            raise ValueError("冲突项已裁决，不可重复裁决：%s" % conflict_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("裁决理由不能为空")
        if not isinstance(adjudicator, str) or not adjudicator.strip():
            raise ValueError("裁决人不能为空")
        if chosen not in item.candidates:
            raise ValueError(
                "裁决值 %r 不在候选列表 %r 内" % (chosen, list(item.candidates))
            )
        updated = replace(
            item,
            status=ConflictStatus.RESOLVED,
            chosen=chosen,
            reason=reason.strip(),
            adjudicator=adjudicator.strip(),
            adjudicated_at=adjudicated_at,
        )
        self._items[conflict_id] = updated
        return updated

    def apply_resolutions(
        self, fields: Mapping[str, FactField]
    ) -> Dict[str, FactField]:
        """把已裁决的冲突回填成**高置信**事实（来源标记为人工裁决）。"""
        resolved_by_field: Dict[str, ConflictItem] = {}
        for item in self.resolved():
            resolved_by_field[item.field] = item

        out: Dict[str, FactField] = {}
        for name, f in fields.items():
            item = resolved_by_field.get(name)
            if item is None:
                out[name] = f
                continue
            out[name] = FactField(
                name=name,
                value=item.chosen,
                confidence=Confidence.HIGH,
                channels=(SourceChannel.ADJUDICATED,),
                routes=(),
                locator=f.locator,
                candidates=item.candidates,
                note="人工裁决（%s）：%s" % (item.adjudicator, item.reason),
            )
        return out
