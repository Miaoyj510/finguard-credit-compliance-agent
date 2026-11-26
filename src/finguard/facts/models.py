"""事实层的数据模型：取值、来源、置信度，以及**结论词闸门**。

三条硬约束：

1. 事实层只输出**事实、取值、来源与置信度**，不输出任何结论性判定
   （PRD 6.2 FR-FACT-07）。闸门见 scan_conclusion_terms()。
2. 低置信字段**不得进入判定链**（PRD 6.2 FR-FACT-03）——见 FactSet.decision_ready()。
3. 置信度只有「高 / 低」两档：双路一致即高，双路不一致（含单路缺失）即低。
   不引入「中」这类没有业务含义的中间档。
"""
from __future__ import annotations

from ..domain.enums import UnresolvedReasonCode

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Confidence",
    "SourceChannel",
    "CONCLUSION_TERMS",
    "CONCLUSION_TERMS_EN",
    "FactField",
    "FactSet",
    "same_value",
    "scan_conclusion_terms",
]


def same_value(a: Any, b: Any) -> bool:
    """事实取值的「严格一致」判定（全事实层唯一口径）。

    比 Python 的 == 更严一档：布尔与数字在 Python 里相等
    （0 == False 为 True），但「抽取到 0」和「抽取到 False」是两回事。
    双路合并、候选去重、裁决值 membership 三处必须共用本函数，
    否则严格性会在其中一处泄漏（评审意见 3）。
    NaN == NaN 为 False，故 NaN 永不判一致。
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


class Confidence(str, Enum):
    HIGH = "高"
    LOW = "低"


class SourceChannel(str, Enum):
    """事实的取数通道。"""

    OCR = "ocr"          # 影像 / 扫描件
    API = "api"          # 只读接口
    ADJUDICATED = "adjudicated"   # 人工裁决后回填


#: 结论性词汇黑名单——事实层输出里出现这些词，就说明越界做了判定。
#: 刻意保守：只收「明确在表达判定结论」的词，不收「风险」「异常」这类描述性词，
#: 避免把「高风险行业」这类正常取值误判成结论。
CONCLUSION_TERMS: Tuple[str, ...] = (
    "违规", "合规", "命中", "不命中", "不适用",
    "通过", "不通过", "予以通过", "予以否决",
    "否决", "放行", "不予放行", "拒绝", "拒绝放款",
    "结论", "建议", "判定", "认定", "涉嫌", "可疑",
)

#: 英文结论词。本项目的**字段名是英文**（declared_revenue / approval_limit …），
#: 只扫中文会漏掉 is_compliant 这类「结论性字段名」。
#:
#: 匹配边界：前后不能是「字母或数字」。下划线**不算**词内字符——因为本项目的
#: 字段名是 snake_case，is_compliant 里的 compliant 必须是独立一段才能命中。
#: 代价：hit_rate 这类含 hit 的字段名会误报——事实层里这类字段本就罕见，
#: 且误报是「响亮地失败」，改名即可，符合严格性取向。
CONCLUSION_TERMS_EN: Tuple[str, ...] = (
    "compliant", "compliance", "non-compliant", "noncompliant", "violation", "violate",
    "reject", "approve", "pass", "hit", "miss",
    "conclusion", "advice", "recommend", "suspicious", "fraud",
    # 常见变形（评审意见 4）：approved / rejected / violations 等曾漏网。
    # 刻意不收 approval / approver——approval_limit / approver_authority
    # 是本域正常字段名，收了会自伤。
    "approved", "rejected", "violations", "hits", "passed", "missed",
)

_EN_PATTERNS = {
    term: re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term), re.IGNORECASE)
    for term in CONCLUSION_TERMS_EN
}


def scan_conclusion_terms(values: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """扫描字段名与取值里的结论性词汇。

    返回 [(字段名, 命中的词)]；空列表 = 没有越界。
    注意：字段名也要扫——把字段命名为 is_compliant 本身就是结论。
    """
    hits: List[Tuple[str, str]] = []
    for name, value in values.items():
        for term in CONCLUSION_TERMS:
            if term in name:
                hits.append((name, term))
        for term, pattern in _EN_PATTERNS.items():
            if pattern.search(name):
                hits.append((name, term))
        text = value if isinstance(value, str) else str(value)
        for term in CONCLUSION_TERMS:
            if term in text:
                hits.append((name, term))
        for term, pattern in _EN_PATTERNS.items():
            if pattern.search(text):
                hits.append((name, term))
    return hits


@dataclass(frozen=True)
class FactField:
    """一条事实字段：取值 + 来源 + 置信度 + 候选（不一致时并列）。"""

    name: str
    value: Any
    confidence: Confidence
    channels: Tuple[SourceChannel, ...] = ()
    routes: Tuple[str, ...] = ()
    locator: str = ""
    candidates: Tuple[Any, ...] = ()
    note: str = ""

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence is Confidence.LOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": self.confidence.value,
            "channels": [c.value for c in self.channels],
            "routes": list(self.routes),
            "locator": self.locator,
            "candidates": list(self.candidates),
            "note": self.note,
        }


@dataclass(frozen=True)
class FactSet:
    """一次抽取 + 核对之后的事实集合。

    decision_ready() 是**判定链的唯一入口**：只有高置信、且没有未裁决冲突的
    字段才会被放行。低置信字段与待裁决字段一律拦在外面（FR-FACT-03）。
    """

    fields: Mapping[str, FactField] = field(default_factory=dict)
    pending_conflicts: Tuple[Any, ...] = ()
    checks: Tuple[Any, ...] = ()
    # 判定链审计钩子（切片 5 SEC-01 测量点）：decision_ready() 每次被调用即通知一次，
    # 记录"谁调用 / 哪些字段进入 / 哪些字段被拦及原因"。
    # compare=False、repr=False：钩子不参与相等性与打印，不影响既有断言。
    decision_audit: Optional[Callable[..., None]] = field(default=None, compare=False, repr=False)

    # ---------- 取值 ----------
    def get(self, name: str) -> Optional[FactField]:
        return self.fields.get(name)

    def value_of(self, name: str) -> Any:
        f = self.fields.get(name)
        if f is None:
            raise KeyError("事实字段不存在：%s" % name)
        return f.value

    # ---------- 闸门 ----------
    def decision_ready(self) -> Dict[str, Any]:
        """返回可以进入判定链的**高置信**事实（低置信与冲突未裁决字段一律拦在外面）。

        本方法是判定链的**唯一入口**。切片 5 在这里挂审计钩子：
        每次进入都记录调用方、进入字段、被拦字段与原因码，
        使 PRD 指标 SEC-01（越权召回 = 0）成为**可测量**的值。
        """
        reasons = self.blocked_reasons()
        blocked = set(reasons)
        ready = {
            name: f.value
            for name, f in self.fields.items()
            if name not in blocked and f.confidence is Confidence.HIGH
        }
        if self.decision_audit is not None:
            self.decision_audit(
                tuple(sorted(ready)),
                tuple(sorted(blocked)),
                dict(reasons),
            )
        return ready

    def blocked_reasons(self) -> Dict[str, str]:
        """被拦字段 -> 原因码。低置信与冲突未裁决分列两类原因。"""
        reasons: Dict[str, str] = {}
        for name in self.low_confidence_fields():
            reasons[name] = UnresolvedReasonCode.LOW_CONFIDENCE.value
        for name in self.unresolved_conflict_fields():
            reasons.setdefault(name, UnresolvedReasonCode.CONFLICT_UNRESOLVED.value)
        return reasons

    def low_confidence_fields(self) -> Tuple[str, ...]:
        return tuple(sorted(n for n, f in self.fields.items() if f.confidence is Confidence.LOW))

    def unresolved_conflict_fields(self) -> Tuple[str, ...]:
        names = set()
        for c in self.pending_conflicts:
            field_name = getattr(c, "field", None)
            if field_name:
                names.add(field_name)
            # 交叉核对冲突涉及两个字段，未裁决时**双方**都不得进判定链
            # （SEC-01 硬指标：低置信/冲突未裁决字段进入判定链次数 = 0）。
            for extra in getattr(c, "involved_fields", ()) or ():
                names.add(extra)
        return tuple(sorted(names))

    def blocked_fields(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.low_confidence_fields()) | set(self.unresolved_conflict_fields())))

    # ---------- 自检 ----------
    def conclusion_hits(self) -> List[Tuple[str, str]]:
        """只扫**事实内容**：字段名、取值、并列候选。

        不扫 locator / note / routes 等元数据（评审意见 6）：出处文件名
        《合规管理办法》、人工裁决理由与裁决人姓名属于来源与留痕，不是
        事实层在做判定，扫它们会把正常出处误杀（FR-FACT-01 要求登记来源）。
        """
        hits = scan_conclusion_terms({n: f.value for n, f in self.fields.items()})
        for n, f in self.fields.items():
            if f.candidates:
                hits.extend(scan_conclusion_terms({n: f.candidates}))
        return hits

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fields": {n: f.to_dict() for n, f in self.fields.items()},
            "decision_ready": self.decision_ready(),
            "blocked_fields": list(self.blocked_fields()),
            "pending_conflicts": [c.to_dict() for c in self.pending_conflicts],
            "checks": [c.to_dict() for c in self.checks],
        }
