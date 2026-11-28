"""金标标注的数据结构与机械比对（切片 8.0）。

**分工前提**：两个 Agent 各自**独立盲标**同一批案件（互不可见对方输出），
本模块负责把两份标注机械比对，得出：

    · 字段级一致率（两个 Agent 的判断是否一致）
    · 分歧清单（哪些字段分歧、怎么分歧）——供人工仲裁
    · 与**技术性真值**的一致率（裁判是否识读正确、规则适用性是否判对）

⚠ 三个数字的性质完全不同，不能混用：
    裁判间一致率      —— 两个人（或两个 Agent）对同一件事的看法是否一致
    裁判 vs 真值一致率 —— 裁判识读 / 规则适用性判断是否正确
    分歧率            —— 需要人工仲裁的比例
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "LABEL_FIELDS",
    "LABEL_SCALES",
    "Annotation",
    "AnnotationError",
    "validate_annotation",
    "compare_annotations",
    "agreement_report",
]

#: 判断性标注的五个字段（**技术性真值不在其中**——那由生成器给出）
LABEL_FIELDS: Tuple[str, ...] = (
    "fact_recognition_ok",
    "rule_applicability",
    "reason_quality",
    "severity_appropriate",
    "is_false_positive",
)

#: 每个字段的合法取值（枚举封闭，防止"自由发挥"变成不可比对的自由文本）
LABEL_SCALES: Mapping[str, Tuple[str, ...]] = {
    "fact_recognition_ok": ("对", "错", "缺漏"),
    "rule_applicability": ("同意", "不同意"),
    # 「不适用」= 本案该维度**无对象**（如无任何命中意见时无理由可评）。
    # 枚举里必须有它，否则裁判被迫给出一个无意义的取值，
    # 一致率统计就掺进了噪声。
    "reason_quality": ("成立", "部分成立", "不成立", "不适用"),
    "severity_appropriate": ("恰当", "偏高", "偏低", "不适用"),
    "is_false_positive": ("是", "否", "无法判断", "不适用"),
}


class AnnotationError(Exception):
    """标注不合法（字段缺失 / 取值不在枚举内 / 案件号缺失）。"""


@dataclass(frozen=True)
class Annotation:
    """一份判断性标注。"""

    case_id: str
    annotator: str
    labels: Mapping[str, str]
    notes: str = ""
    labeled_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "annotator": self.annotator,
            "labels": dict(self.labels),
            "notes": self.notes,
            "labeled_at": self.labeled_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Annotation":
        return cls(
            case_id=data.get("case_id", ""),
            annotator=data.get("annotator", ""),
            labels=dict(data.get("labels", {}) or {}),
            notes=data.get("notes", ""),
            labeled_at=data.get("labeled_at", ""),
        )


def validate_annotation(annotation: Annotation) -> None:
    """校验一份标注：字段齐、取值合法、案件号与标注人非空。

    **枚举封闭**是双盲比对能机械化的前提——如果取值是自由文本，
    比对就退化成人工阅读，分歧统计也无从自动算。
    """
    if not annotation.case_id.strip():
        raise AnnotationError("标注缺 case_id")
    if not annotation.annotator.strip():
        raise AnnotationError("标注缺 annotator")
    missing = [f for f in LABEL_FIELDS if f not in annotation.labels]
    if missing:
        raise AnnotationError(
            "标注 %s 缺字段：%s" % (annotation.case_id, "、".join(missing)))
    for field_name, value in annotation.labels.items():
        if field_name not in LABEL_SCALES:
            raise AnnotationError(
                "标注 %s 含未知字段 %r" % (annotation.case_id, field_name))
        allowed = LABEL_SCALES[field_name]
        if value not in allowed:
            raise AnnotationError(
                "标注 %s 的 %s 取值 %r 非法（合法：%s）"
                % (annotation.case_id, field_name, value, " / ".join(allowed)))


def compare_annotations(
    left: Annotation,
    right: Annotation,
) -> Dict[str, Any]:
    """比对两份标注（同一案件）。返回分歧明细。"""
    if left.case_id != right.case_id:
        raise AnnotationError(
            "不能比对不同案件：%s vs %s" % (left.case_id, right.case_id))
    diffs: List[Dict[str, Any]] = []
    for field_name in LABEL_FIELDS:
        a = left.labels.get(field_name, "")
        b = right.labels.get(field_name, "")
        if a != b:
            diffs.append({"field": field_name, "left": a, "right": b})
    return {
        "case_id": left.case_id,
        "agree": not diffs,
        "fields_compared": len(LABEL_FIELDS),
        "fields_agree": len(LABEL_FIELDS) - len(diffs),
        "diffs": diffs,
    }


def agreement_report(
    left: Sequence[Annotation],
    right: Sequence[Annotation],
    *,
    labels: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """两份标注的总体一致率与分歧清单。

    ``labels`` 可选：给每个分歧加一句**仲裁提示**（谁是权威），
    例如规则适用性以技术性真值为准、严重度以规则库为准。
    """
    right_by_case = {a.case_id: a for a in right}
    compared = 0
    agree = 0
    case_agree = 0
    diffs: List[Dict[str, Any]] = []
    per_field: Dict[str, Dict[str, int]] = {
        f: {"agree": 0, "total": 0} for f in LABEL_FIELDS
    }
    for a in left:
        b = right_by_case.get(a.case_id)
        if b is None:
            continue
        result = compare_annotations(a, b)
        compared += 1
        agree += result["fields_agree"]
        if result["agree"]:
            case_agree += 1
        for field_name in LABEL_FIELDS:
            per_field[field_name]["total"] += 1
            if a.labels.get(field_name) == b.labels.get(field_name):
                per_field[field_name]["agree"] += 1
        if result["diffs"]:
            for diff in result["diffs"]:
                item = dict(diff)
                item["case_id"] = a.case_id
                if labels and diff["field"] in labels:
                    item["adjudication_hint"] = labels[diff["field"]]
                diffs.append(item)
    return {
        "cases_compared": compared,
        "cases_agree": case_agree,
        "case_agreement_rate": round(case_agree / compared, 6) if compared else None,
        "fields_compared": compared * len(LABEL_FIELDS),
        "fields_agree": agree,
        "field_agreement_rate": round(agree / (compared * len(LABEL_FIELDS)), 6) if compared else None,
        "per_field": {
            name: {
                "agree": stats["agree"],
                "total": stats["total"],
                "rate": round(stats["agree"] / stats["total"], 6) if stats["total"] else None,
            }
            for name, stats in per_field.items()
        },
        "diff_count": len(diffs),
        "diffs": diffs,
    }