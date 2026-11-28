"""贷后领域模型（PRD 6.4 POST 组、8.5 状态清单 S7—S10）。

四类核心对象：

    PostDisposition   贷后处置**四档**（FR-POST-05）——与贷前的「风险处置建议」
                      是**两套不同枚举**，不可混用
    Alert             预警：带触发规则编号 + 版本 + 证据快照 + 命中时点（FR-POST-03）
    Verification      核查初核结论（FR-POST-06）
    PostCaseView      放款后案件的**只读视图**——贷后循环只读它，不碰主流程状态

两个容易写错、这里钉死的地方：

1. **规则版本按放款日取，命中时点按扫描日记**。三键里的 ``business_date``
   对贷后案件是**放款日 / 业务发生日**，所以贷前贷后同一 ``rule_id`` 取到
   同一版本（FR-POST-02「version 语义一致」）；而"命中时点"是扫描日，
   单独一个字段。两个时间维度**不混**——混了就会出现"用今天的规则判放款日"，
   正是切片 1 反复强调要避免的事。

2. **提前量以放款日为基准**，不是以系统上线日为基准（FR-POST-04）。
   基准日来自案件的三键，与系统何时上线无关。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "PostDisposition",
    "PRE_DISPOSITION_LABELS",
    "VerificationStatus",
    "UnverifiedReason",
    "DISPOSITION_ACTIONS",
    "AlertLevel",
    "AlertStatus",
    "Alert",
    "VerificationOutcome",
    "Verification",
    "PostCaseView",
    "PostMetrics",
    "compute_post_metrics",
    "freeze_snapshot",
]


class PostDisposition(str, Enum):
    """贷后预警处置**四档**（FR-POST-05）。

    刻意与贷前的「风险处置建议」分开：两者的决策对象不同
    （贷前是对**一笔申请**给结论，贷后是对**一条预警**给处置），
    枚举值与档位数都不同，混用会让采纳率口径失真。
    """

    CLOSE = "关闭"
    FOLLOW_UP = "跟进"
    ESCALATE_ATTENTION = "上调关注"
    SITE_INSPECTION = "触发现场检查"


#: PRD 8.7 GATE-B 的**贷前**风险处置建议四档——**仅作文档化对照**。
#: 贷后四档（PostDisposition）与它**刻意用词不同**（「调整关注」vs「上调关注」），
#: 两套枚举的决策对象也不同：贷前是对一笔申请给结论，贷后是对一条预警给处置。
#: 混用会让采纳率口径失真，所以这里把两套摆在一起做**互斥断言**。
PRE_DISPOSITION_LABELS: Tuple[str, ...] = ("观察", "复核", "调整关注", "上报")


#: 每档绑定的**人工作业要求**（FR-POST-05：每档对应作业卡片）
DISPOSITION_ACTIONS: Mapping[PostDisposition, str] = MappingProxyType({
    PostDisposition.CLOSE: "记录关闭理由（须引用核查结论），归档该预警，不再纳入跟踪",
    PostDisposition.FOLLOW_UP: "在贷后检查台账登记跟进事项，设定下次复核日期与责任人",
    PostDisposition.ESCALATE_ATTENTION: "上调该笔贷款的贷后关注等级，纳入下期重点检查名单",
    PostDisposition.SITE_INSPECTION: "生成现场检查任务单，指定检查人与反馈时限",
})


class AlertLevel(str, Enum):
    """预警等级。"""

    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class AlertStatus(str, Enum):
    """预警状态。"""

    NEW = "待核查"
    UNDER_REVIEW = "核查中"
    VERIFIED = "已核查"
    DISPOSED = "已处置"
    DISMISSED = "已判误报"


def freeze_snapshot(value: Any) -> Any:
    """深拷贝并冻结成只读结构。

    证据快照必须**不可被后续覆盖**（FR-POST-03）：预警生成之后，
    事实层再怎么更新取值，都不能改写"命中当时看到了什么"。
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze_snapshot(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_snapshot(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """把冻结结构还原成可序列化的普通结构。"""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Alert:
    """一条贷后预警。

    ``rule_id`` / ``rule_version``：命中的规则与**当时适用**的版本
    ``hit_at``：命中时点（扫描日）
    ``baseline_date``：提前量基准（放款日 / 业务发生日，来自三键）
    ``lead_time_days``：提前量天数 = hit_at - baseline_date
    ``evidence``：**不可变**的证据快照
    """

    alert_id: str
    case_id: str
    rule_id: str
    rule_version: str
    hit_at: str
    baseline_date: str
    lead_time_days: int
    level: AlertLevel
    evidence: Mapping[str, Any]
    status: AlertStatus = AlertStatus.NEW
    disposition: Optional[PostDisposition] = None
    disposed_by: Optional[str] = None
    disposed_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.lead_time_days < 0:
            raise ValueError("提前量不能为负：命中时点早于基准日")
        object.__setattr__(self, "evidence", freeze_snapshot(self.evidence))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "case_id": self.case_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "hit_at": self.hit_at,
            "baseline_date": self.baseline_date,
            "lead_time_days": self.lead_time_days,
            "level": self.level.value,
            "evidence": _thaw(self.evidence),
            "status": self.status.value,
            "disposition": self.disposition.value if self.disposition else None,
            "disposed_by": self.disposed_by,
            "disposed_at": self.disposed_at,
        }

    def with_disposition(
        self,
        disposition: PostDisposition,
        disposed_by: str,
        disposed_at: str,
    ) -> "Alert":
        """带处置档位的**新**预警对象（原对象不变）。"""
        return Alert(
            alert_id=self.alert_id,
            case_id=self.case_id,
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            hit_at=self.hit_at,
            baseline_date=self.baseline_date,
            lead_time_days=self.lead_time_days,
            level=self.level,
            evidence=self.evidence,
            status=AlertStatus.DISPOSED,
            disposition=disposition,
            disposed_by=disposed_by,
            disposed_at=disposed_at,
        )


class VerificationStatus(str, Enum):
    """核查状态。

    FR-POST-06 的验收标准是「每条预警均有核查记录**或**明确『未核查』状态与原因」——
    所以「没核查」必须是一个**显式状态 + 原因码**，不能是"查不到记录"这种沉默。
    """

    CHECKED = "已核查"
    NOT_CHECKED = "未核查"


@dataclass(frozen=True)
class UnverifiedReason:
    """一条预警「未核查」的原因。"""

    alert_id: str
    reason_code: str
    detail: str
    at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "at": self.at,
        }


class VerificationOutcome(str, Enum):
    """核查初核结论（FR-POST-06）。"""

    CONFIRMED = "证据支持"
    UNCONFIRMED = "证据不支持"
    INSUFFICIENT = "证据不足"


@dataclass(frozen=True)
class Verification:
    """一条预警的核查记录。"""

    alert_id: str
    outcome: VerificationOutcome
    checked_at: str
    checked_by: str
    rationale: str
    evidence_gaps: Tuple[str, ...] = ()
    recommend_escalation: bool = False
    supplemented_fields: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "outcome": self.outcome.value,
            "checked_at": self.checked_at,
            "checked_by": self.checked_by,
            "rationale": self.rationale,
            "evidence_gaps": list(self.evidence_gaps),
            "recommend_escalation": self.recommend_escalation,
            "supplemented_fields": list(self.supplemented_fields),
        }


@dataclass(frozen=True)
class PostCaseView:
    """放款后案件的**只读视图**。

    贷后循环只拿这个视图，不接触主流程的 ``CaseFile``——
    这是「常驻循环与主流程解耦」（FR-POST-01）的**结构性**保证：
    没有引用，就无从阻塞。
    """

    case_id: str
    product_type: str
    org_level: str
    baseline_date: str          # 放款日 / 业务发生日（三键里的 business_date）
    rule_bindings: Tuple[Tuple[str, str], ...] = ()   # (rule_id, version)
    # 事实**取值**：扁平映射，直接喂给规则引擎（切片 2 的事实契约）
    facts: Mapping[str, Any] = field(default_factory=dict)
    # 字段元信息：字段名 -> {channel / confidence / locator}
    # 单独一层，是因为引擎要扁平值，而证据快照要出处（切片 3 的来源通道）。
    field_meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", freeze_snapshot(self.facts))
        object.__setattr__(self, "field_meta", freeze_snapshot(self.field_meta))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "product_type": self.product_type,
            "org_level": self.org_level,
            "baseline_date": self.baseline_date,
            "rule_bindings": [list(b) for b in self.rule_bindings],
            "facts": _thaw(self.facts),
            "field_meta": _thaw(self.field_meta),
        }


def compute_post_metrics(
    alerts: Sequence["Alert"],
    verifications: Sequence["Verification"] = (),
    unverified: Sequence["UnverifiedReason"] = (),
) -> "PostMetrics":
    """按统一口径算贷后指标（FR-POST-07），并按规则下钻。

        采纳率   = 被采纳的预警 / 全部预警（被采纳 = 处置档位非「关闭」）
        误报率   = 判为「证据不支持」且已关闭的预警 / 已核查预警

    漏报率**不在这里算**：它需要"实际发生了但系统没预警"的外部对照集，
    自建合成语料上没有 ground truth。口径写在 ``data/external/README.md``，
    等切片 8 的对照集就位再补——不拿编造的数充指标。
    """
    verified_by_alert = {v.alert_id: v for v in verifications}
    per_rule: Dict[str, Dict[str, int]] = {}
    adopted = 0
    false_positive = 0
    verified_count = 0

    for alert in alerts:
        bucket = per_rule.setdefault(
            alert.rule_id, {"total": 0, "adopted": 0, "verified": 0, "false_positive": 0}
        )
        bucket["total"] += 1
        verification = verified_by_alert.get(alert.alert_id)
        if verification is not None:
            verified_count += 1
            bucket["verified"] += 1
        if alert.disposition is not None and alert.disposition is not PostDisposition.CLOSE:
            adopted += 1
            bucket["adopted"] += 1
        if (
            verification is not None
            and verification.outcome is VerificationOutcome.UNCONFIRMED
            and alert.disposition is PostDisposition.CLOSE
        ):
            false_positive += 1
            bucket["false_positive"] += 1

    return PostMetrics(
        total_alerts=len(alerts),
        adopted=adopted,
        false_positive=false_positive,
        verified=verified_count,
        unverified=len(unverified),
        per_rule=tuple((rule, dict(counts)) for rule, counts in sorted(per_rule.items())),
    )


@dataclass(frozen=True)
class PostMetrics:
    """贷后指标（FR-POST-07）。

    口径写死在这里，**与 [16] 招行「贷后监测结果采纳率 68%」同口径**：

        采纳率   = 被采纳的预警 / 全部预警
                   （被采纳 = 处置档位不是「关闭」）
        误报率   = 被判为「证据不支持」且关闭的预警 / 已核查预警

    **漏报率本切片不实现**：它需要"实际发生了但系统没预警"的外部对照集，
    在自建合成语料上没有 ground truth。口径写在 ``data/external/README.md``，
    等切片 8 的对照集就位再补——**不拿一个编出来的数冒充指标**。
    """

    total_alerts: int = 0
    adopted: int = 0
    false_positive: int = 0
    verified: int = 0
    unverified: int = 0
    per_rule: Tuple[Tuple[str, Dict[str, int]], ...] = ()

    @property
    def adoption_rate(self) -> Optional[float]:
        if not self.total_alerts:
            return None
        return round(self.adopted / self.total_alerts, 6)

    @property
    def false_positive_rate(self) -> Optional[float]:
        if not self.verified:
            return None
        return round(self.false_positive / self.verified, 6)

    def drill_down(self) -> Dict[str, Dict[str, int]]:
        """按规则下钻（FR-POST-07：可下钻到单条规则）。"""
        return {rule: dict(counts) for rule, counts in self.per_rule}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_alerts": self.total_alerts,
            "adopted": self.adopted,
            "false_positive": self.false_positive,
            "verified": self.verified,
            "unverified": self.unverified,
            "adoption_rate": self.adoption_rate,
            "false_positive_rate": self.false_positive_rate,
            "per_rule": {rule: dict(c) for rule, c in self.per_rule},
            "caliber": {
                "adoption_rate": "被采纳预警 / 全部预警；被采纳 = 处置档位非「关闭」",
                "false_positive_rate": "判为证据不支持且关闭的预警 / 已核查预警",
                "miss_rate": "需外部对照集，本切片不实现（不以编造的数充指标）",
            },
        }