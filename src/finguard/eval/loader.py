# -*- coding: utf-8 -*-
"""金标加载与评测集派生（PRD 12.3 · 切片 8.1）。

**为什么要有这一层**：评测框架要能**真跑管线**再和金标比对，
前提是「金标」与「跑批输入」是同一个东西。这里把金标 JSON 读成
**结构化视图**，并派生三个评测面：

    · **EVAL-VER**（版本对）——由金标 ``gold.binding`` 派生：
      一条 (案件, 规则) 应取到哪个版本，或应落到哪个「须人工判定」原因码。
    · **EVAL-REV**（整件）——金标案件全集，跑完整判定 + 意见生成链路，
      与金标 ``gold.verdicts`` / ``gold.coverage_prd`` 比对。
    · **EVAL-FACT**（字段级子集）——由金标 ``field_samples`` 派生，
      专门评测事实层的抽取、合并与拦截。

**一个关键口径（金标 facts ≠ 事实层原始抽取）**：
金标 ``facts`` 表是**已裁决事实集**（人工裁决后的取值），因此它可以
直接进判定链；``field_samples`` 记录的是**事实层自身的抽取样本**
（两路各抽到什么）。所以：

    · 跑 EVAL-REV 时，事实集按「全部高置信、已裁决」构造；
    · 跑 EVAL-FACT 时，按 ``field_samples`` 重建**两路抽取**再进事实层。

两者不冲突——金标 annotator 在 014/023 两件里同时给了
``facts``（裁决后取值）与 ``field_samples``（两路分歧取值），
正说明这就是同一个案件的两层视角。

⚠ 样本量口径：首批 30 件是**口径试算**，不是正式判定样本量
（PRD 12.3 的正式口径是 200 件 / 120 字段 / 150 件贷后）。
本模块不负责放量，只保证放量后可直接复用同一套派生逻辑。
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.case import TripleKey
from ..facts.extract import RouteResult
from ..facts.models import Confidence, FactField, FactSet, SourceChannel

__all__ = [
    "FACT_CATEGORIES",
    "GoldBinding",
    "GoldVerdict",
    "GoldFactSample",
    "GoldCase",
    "GoldSet",
    "VersionPair",
    "FactSampleCase",
    "EvalSets",
    "load_gold",
    "check_fact_category",
    "default_gold_path",
    "derive_eval_sets",
    "decision_factset",
    "fact_routes",
]

#: 事实层字段样本的四类（金标 ANNOTATION_GUIDE.md v1 的分类学）
FACT_CATEGORIES: Tuple[str, ...] = ("双路一致", "双路不一致", "跨渠道缺失", "勾稽冲突")

#: 金标根目录
GOLD_DIR = os.path.join("data", "gold")
DEFAULT_GOLD_FILE = os.path.join(GOLD_DIR, "eval_rev_batch1.json")
DEFAULT_INJECTION_FILE = os.path.join(GOLD_DIR, "injection_samples.json")


def check_fact_category(case_id: str, category: str) -> str:
    """字段样本类别必须是封闭枚举——自由文本会让评测口径漂移。"""
    if category not in FACT_CATEGORIES:
        raise ValueError(
            "案件 %s 的字段样本类别 %r 不在封闭枚举 %s 内"
            % (case_id, category, "、".join(FACT_CATEGORIES))
        )
    return category


def _repo_root() -> str:
    """仓库根目录（src/finguard/eval/loader.py 往上四级）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _parse_date(text: str) -> date:
    parts = text.split("-")
    if len(parts) != 3:
        raise ValueError("业务发生日格式非法：%r" % (text,))
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


# ---------------------------------------------------------------- 金标结构
@dataclass(frozen=True)
class GoldBinding:
    """金标里的一条规则绑定期望。

    ``version`` 为 None 表示**须人工判定**，此时 ``reason_code`` 必非空
    （NO_VERSION_MATCH / AMBIGUOUS_VERSION）——这正是 PRD Q-09 的判分点。
    """

    rule_id: str
    version: Optional[str]
    basis: str
    reason_code: Optional[str]

    @property
    def needs_human(self) -> bool:
        return self.version is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "basis": self.basis,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class GoldVerdict:
    """金标里的一条判定期望。

    ``verdict`` 取值：hit / miss / unresolved。
    ``severity`` 只在命中时非空（未命中不产生严重度）。
    """

    rule_id: str
    version: Optional[str]
    verdict: str
    severity: Optional[str]
    fact_refs: Tuple[str, ...]
    reason_code: Optional[str]

    @property
    def judged(self) -> bool:
        """是否已判定（命中或未命中）。"""
        return self.verdict in ("hit", "miss")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "verdict": self.verdict,
            "severity": self.severity,
            "fact_refs": list(self.fact_refs),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class GoldFactSample:
    """一个字段级事实样本（EVAL-FACT 的最小单位）。

    四类样本的字段含义各不相同，装在一个 dataclass 里是**为了对齐金标
    JSON 原样**（金标由评审方手写，不改它的形状），不是为了让四类共享
    同一套字段——读的时候按 category 分支。
    """

    field: str
    value: Any
    category: str
    channel_a: Optional[str] = None
    channel_b: Optional[str] = None
    channel_a_value: Any = None
    channel_b_value: Any = None
    agree: Optional[bool] = None
    paired_field: Optional[str] = None
    paired_value: Any = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldFactSample":
        return cls(
            field=data.get("field", ""),
            value=data.get("value"),
            category=data.get("category", ""),
            channel_a=data.get("channel_a"),
            channel_b=data.get("channel_b"),
            channel_a_value=data.get("channel_a_value"),
            channel_b_value=data.get("channel_b_value"),
            agree=data.get("agree"),
            paired_field=data.get("paired_field"),
            paired_value=data.get("paired_value"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "category": self.category,
            "channel_a": self.channel_a,
            "channel_b": self.channel_b,
            "channel_a_value": self.channel_a_value,
            "channel_b_value": self.channel_b_value,
            "agree": self.agree,
            "paired_field": self.paired_field,
            "paired_value": self.paired_value,
        }


@dataclass(frozen=True)
class GoldCase:
    """一件金标案件。"""

    case_id: str
    eval_set: str
    batch: int
    bucket: str
    triple_key: TripleKey
    facts: Mapping[str, Any]
    missing_facts: Tuple[str, ...]
    bindings: Tuple[GoldBinding, ...]
    verdicts: Tuple[GoldVerdict, ...]
    coverage_prd: Mapping[str, int]
    field_samples: Tuple[GoldFactSample, ...]
    annotator: str
    labeled_at: str
    guide_version: str
    note: str
    eval_fact_subset: bool

    # ---- 便捷视图 ----
    @property
    def binding_map(self) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        return {b.rule_id: (b.version, b.reason_code) for b in self.bindings}

    @property
    def verdict_map(self) -> Dict[str, GoldVerdict]:
        return {v.rule_id: v for v in self.verdicts}

    @property
    def expected_judged(self) -> int:
        """金标口径下「已判定」的条数（命中 + 未命中）。"""
        return sum(1 for v in self.verdicts if v.judged)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "eval_set": self.eval_set,
            "batch": self.batch,
            "bucket": self.bucket,
            "triple_key": self.triple_key.to_dict(),
            "facts": dict(self.facts),
            "missing_facts": list(self.missing_facts),
            "bindings": [b.to_dict() for b in self.bindings],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "coverage_prd": dict(self.coverage_prd),
            "field_samples": [s.to_dict() for s in self.field_samples],
            "annotator": self.annotator,
            "labeled_at": self.labeled_at,
            "guide_version": self.guide_version,
            "note": self.note,
            "eval_fact_subset": self.eval_fact_subset,
        }


@dataclass(frozen=True)
class GoldSet:
    """一批金标。"""

    eval_set: str
    batch: int
    gold_version: str
    guide_version: str
    annotator: str
    annotated_at: str
    bucket_quota: Mapping[str, int]
    rule_store: str
    cases: Tuple[GoldCase, ...]
    source_path: str = ""

    def by_id(self, case_id: str) -> GoldCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError("金标里没有案件 %s" % case_id)

    @property
    def sample_size(self) -> Dict[str, int]:
        """评测面样本量（口径试算用）。"""
        return {
            "EVAL-REV": len(self.cases),
            "EVAL-VER": sum(len(c.bindings) for c in self.cases),
            "EVAL-FACT": sum(len(c.field_samples) for c in self.cases),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eval_set": self.eval_set,
            "batch": self.batch,
            "gold_version": self.gold_version,
            "guide_version": self.guide_version,
            "annotator": self.annotator,
            "annotated_at": self.annotated_at,
            "bucket_quota": dict(self.bucket_quota),
            "rule_store": self.rule_store,
            "source_path": self.source_path,
            "cases": [c.to_dict() for c in self.cases],
        }


# ---------------------------------------------------------------- 派生评测面
@dataclass(frozen=True)
class VersionPair:
    """EVAL-VER 的一个版本对：某件业务的某条规则应取哪一版。"""

    case_id: str
    rule_id: str
    expected_version: Optional[str]
    expected_reason_code: Optional[str]

    @property
    def needs_human(self) -> bool:
        return self.expected_version is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rule_id": self.rule_id,
            "expected_version": self.expected_version,
            "expected_reason_code": self.expected_reason_code,
        }


@dataclass(frozen=True)
class FactSampleCase:
    """EVAL-FACT 的一个案件视图：含该案全部字段样本。"""

    case_id: str
    bucket: str
    samples: Tuple[GoldFactSample, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "bucket": self.bucket,
            "samples": [s.to_dict() for s in self.samples],
        }


@dataclass(frozen=True)
class EvalSets:
    """三个评测面的集合。"""

    ver: Tuple[VersionPair, ...]
    rev: Tuple[GoldCase, ...]
    fact: Tuple[FactSampleCase, ...]

    @property
    def sizes(self) -> Dict[str, int]:
        return {
            "EVAL-VER": len(self.ver),
            "EVAL-REV": len(self.rev),
            "EVAL-FACT": sum(len(f.samples) for f in self.fact),
        }


def derive_eval_sets(gold: GoldSet) -> EvalSets:
    """由金标派生三个评测面。"""
    ver: List[VersionPair] = []
    for case in gold.cases:
        for binding in case.bindings:
            ver.append(
                VersionPair(
                    case_id=case.case_id,
                    rule_id=binding.rule_id,
                    expected_version=binding.version,
                    expected_reason_code=binding.reason_code,
                )
            )
    fact: List[FactSampleCase] = []
    for case in gold.cases:
        if not case.field_samples:
            continue
        fact.append(
            FactSampleCase(
                case_id=case.case_id,
                bucket=case.bucket,
                samples=case.field_samples,
            )
        )
    return EvalSets(
        ver=tuple(ver),
        rev=tuple(gold.cases),
        fact=tuple(fact),
    )


# ---------------------------------------------------------------- 加载
def default_gold_path() -> str:
    """默认金标文件（仓库内相对路径的绝对形式）。"""
    root = _repo_root()
    return os.path.join(root, DEFAULT_GOLD_FILE)


def default_injection_path() -> str:
    root = _repo_root()
    return os.path.join(root, DEFAULT_INJECTION_FILE)


def load_gold(path: Optional[str] = None) -> GoldSet:
    """读金标文件。结构不符直接报错——**不静默补字段**。"""
    target = path or default_gold_path()
    with io.open(target, encoding="utf-8") as fh:
        raw = json.load(fh)

    cases: List[GoldCase] = []
    for item in raw.get("cases", ()):
        triple = item.get("triple_key") or {}
        bindings = tuple(
            GoldBinding(
                rule_id=b.get("rule_id", ""),
                version=b.get("version"),
                basis=b.get("basis", ""),
                reason_code=b.get("reason_code"),
            )
            for b in item.get("gold", {}).get("binding", ())
        )
        verdicts = tuple(
            GoldVerdict(
                rule_id=v.get("rule_id", ""),
                version=v.get("version"),
                verdict=v.get("verdict", ""),
                severity=v.get("severity"),
                fact_refs=tuple(v.get("fact_refs", ()) or ()),
                reason_code=v.get("reason_code"),
            )
            for v in item.get("gold", {}).get("verdicts", ())
        )
        samples = tuple(
            GoldFactSample.from_dict(s) for s in item.get("field_samples", ()) or ()
        )
        for sample in samples:
            check_fact_category(item.get("case_id", ""), sample.category)
        cases.append(
            GoldCase(
                case_id=item.get("case_id", ""),
                eval_set=item.get("eval_set", ""),
                batch=int(item.get("batch", 0)),
                bucket=item.get("bucket", ""),
                triple_key=TripleKey(
                    business_date=_parse_date(triple.get("business_date", "")),
                    product_type=triple.get("product_type", ""),
                    org_level=triple.get("org_level", ""),
                ),
                facts=dict(item.get("facts", {}) or {}),
                missing_facts=tuple(item.get("missing_facts", ()) or ()),
                bindings=bindings,
                verdicts=verdicts,
                coverage_prd=dict(item.get("gold", {}).get("coverage_prd", {}) or {}),
                field_samples=samples,
                annotator=item.get("annotator", ""),
                labeled_at=item.get("annotated_at", ""),
                guide_version=item.get("guide_version", ""),
                note=item.get("note", ""),
                eval_fact_subset=bool(item.get("eval_fact_subset", False)),
            )
        )

    return GoldSet(
        eval_set=raw.get("eval_set", ""),
        batch=int(raw.get("batch", 0)),
        gold_version=raw.get("gold_version", ""),
        guide_version=raw.get("guide_version", ""),
        annotator=raw.get("annotator", ""),
        annotated_at=raw.get("annotated_at", ""),
        bucket_quota=dict(raw.get("bucket_quota", {}) or {}),
        rule_store=raw.get("rule_store", ""),
        cases=tuple(cases),
        source_path=target,
    )


# ---------------------------------------------------------------- 事实集构造
def decision_factset(case: GoldCase) -> FactSet:
    """按**已裁决事实集**构造判定链入口（EVAL-REV 用）。

    金标 ``facts`` 表是人工裁决后的取值，全部高置信、来源登记为
    ocr + api 两条通道——与真实链路「裁决回填」的语义一致。
    缺失事实不在表里，因此不会进判定链（引擎按 MISSING_FACT 降级）。
    """
    fields = {
        name: FactField(
            name=name,
            value=value,
            confidence=Confidence.HIGH,
            channels=(SourceChannel.OCR, SourceChannel.API),
            routes=("vote", "rule_check"),
            locator="gold://%s/%s" % (case.case_id, name),
            note="金标已裁决事实",
        )
        for name, value in case.facts.items()
    }
    return FactSet(fields=fields)


def fact_routes(case: GoldCase) -> Tuple[RouteResult, RouteResult]:
    """按 ``field_samples`` 重建**两路抽取**（EVAL-FACT 用）。

    重建规则（逐类）：

        · 双路一致    —— A、B 两路取同一取值
        · 双路不一致  —— A 取 channel_a_value、B 取 channel_b_value
        · 跨渠道缺失  —— A 路给出（取值可能为空），B 路没有该字段
        · 勾稽冲突    —— 两路各自一致，冲突出现在两个字段之间

    未出现在样本里的字段按「两路一致」处理（默认高置信）。
    """
    route_a: Dict[str, Any] = {}
    route_b: Dict[str, Any] = {}

    for name, value in case.facts.items():
        route_a[name] = value
        route_b[name] = value

    for sample in case.field_samples:
        name = sample.field
        if sample.category == "双路一致":
            route_a[name] = sample.value
            route_b[name] = sample.value
        elif sample.category == "双路不一致":
            route_a[name] = sample.channel_a_value
            route_b[name] = sample.channel_b_value
        elif sample.category == "跨渠道缺失":
            route_a[name] = sample.value
            route_b.pop(name, None)
        elif sample.category == "勾稽冲突":
            route_a[name] = sample.value
            route_b[name] = sample.value
        else:
            raise ValueError("未知字段样本类别：%r" % (sample.category,))

    return (
        RouteResult(route="vote", channel=SourceChannel.OCR, values=route_a),
        RouteResult(route="rule_check", channel=SourceChannel.API, values=route_b),
    )
