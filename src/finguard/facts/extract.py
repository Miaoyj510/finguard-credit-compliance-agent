"""双路抽取与合并。

PRD 6.2 FR-FACT-02：字段抽取采用**多模型投票 + 规则校验双路**；
两路一致标记为高置信，不一致标记为低置信。

本切片先用**桩件**跑通逻辑（真实 OCR / 真实模型调用留到切片 9 接入，
理由见 PRD 7.6.7 选型轴①②③⑨）。换实现时，本模块的契约不变。

关键语义：
    · 两路都有且相等            -> 高置信
    · 两路都有但不相等          -> 低置信 + 并列两个候选值
    · 只有一路抽到              -> 低置信 + 单一候选值
    · 两路都没抽到              -> 该字段不存在
    规则校验路「拒绝」的字段，等同于该路没抽到。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .models import Confidence, FactField, SourceChannel

__all__ = ["RouteResult", "merge_dual", "distinct_candidates"]


@dataclass(frozen=True)
class RouteResult:
    """一路抽取的结果。

    route 是「哪一路」（vote = 多模型投票，rule_check = 规则校验）；
    channel 是「事实来自哪个取数通道」（ocr / api）。
    """

    route: str
    channel: SourceChannel
    values: Mapping[str, Any] = field(default_factory=dict)
    rejected: Tuple[str, ...] = ()            # 被该路判定为不可用的字段
    locators: Mapping[str, str] = field(default_factory=dict)

    def usable(self) -> Dict[str, Any]:
        """该路真正给出的取值（排除被拒绝的字段）。"""
        rejected = set(self.rejected)
        return {k: v for k, v in self.values.items() if k not in rejected}


def _same_value(a: Any, b: Any) -> bool:
    """两路取值是否「一致」。

    比 Python 的 == 更严一档：布尔与数字在 Python 里相等
    （0 == False 为 True），但「抽取到 0」和「抽取到 False」是两回事，
    必须判为不一致，交人工裁决。
    """
    a_bool = isinstance(a, bool)
    b_bool = isinstance(b, bool)
    if a_bool != b_bool:
        return False
    return a == b


def distinct_candidates(values) -> Tuple[Any, ...]:
    """去重保序的候选值列表。"""
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return tuple(out)


def merge_dual(route_a: RouteResult, route_b: RouteResult) -> Dict[str, FactField]:
    """合并两路抽取结果。

    返回 {字段名: FactField}。置信度只取决于两路是否**一致**，
    与取值的大小、类型无关。
    """
    a = route_a.usable()
    b = route_b.usable()
    names = sorted(set(a) | set(b))

    merged: Dict[str, FactField] = {}
    for name in names:
        in_a, in_b = name in a, name in b
        if in_a and in_b:
            if _same_value(a[name], b[name]):
                merged[name] = FactField(
                    name=name,
                    value=a[name],
                    confidence=Confidence.HIGH,
                    channels=(route_a.channel, route_b.channel),
                    routes=(route_a.route, route_b.route),
                    locator=route_a.locators.get(name, "") or route_b.locators.get(name, ""),
                    note="两路一致",
                )
            else:
                merged[name] = FactField(
                    name=name,
                    value=a[name],
                    confidence=Confidence.LOW,
                    channels=(route_a.channel, route_b.channel),
                    routes=(route_a.route, route_b.route),
                    locator=route_a.locators.get(name, "") or route_b.locators.get(name, ""),
                    candidates=distinct_candidates([a[name], b[name]]),
                    note="两路取值不一致，须人工裁决",
                )
        else:
            only_value = a[name] if in_a else b[name]
            only_route = route_a if in_a else route_b
            merged[name] = FactField(
                name=name,
                value=only_value,
                confidence=Confidence.LOW,
                channels=(only_route.channel,),
                routes=(only_route.route,),
                locator=only_route.locators.get(name, ""),
                candidates=distinct_candidates([only_value]),
                note="仅一路抽到该字段，须人工裁决",
            )
    return merged
