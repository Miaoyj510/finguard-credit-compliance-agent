"""案件卷宗：六层内容与齐全性校验（PRD 8.9、FR-GATE-05、P-10）。

六层（PRD 8.9 原文）：

    L1 事实层快照      字段取值、来源通道、置信度、冲突裁决结果与裁决人
    L2 规则版本快照    rule_id + version + effective_from/to + 三键绑定依据 + 命中路径
    L3 依据文档快照    chunk_id、原文 quote、来源文号与发布日期
    L4 审查意见层      结构化意见 + 覆盖度说明 + 严重度 + 未判定原因码 + AgentSpec 版本
    L5 人审签核层      签核人、时间戳、签核结论、改写前后双版本、修改理由、升级记录
    L6 处置与贷后闭环层 风险处置决定、贷后预警、核查记录、预警处置决定、处置结果

**两档齐全性校验**：齐全（COMPLETE）/ 不完整（INCOMPLETE）。
不完整的卷宗**不可用于监管取证**（PRD 8.9 完整性校验）。

每层带内容哈希：写入时算一次，校验时重算——**卷宗层用内容哈希校验**
（PRD 8.9 不可篡改），与事件日志的哈希链是两道独立的防线。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "DossierError",
    "DossierLayer",
    "Completeness",
    "LayerSnapshot",
    "DossierVerification",
    "Dossier",
    "content_hash",
    "facts_layer_payload",
    "rules_layer_payload",
    "documents_layer_payload",
    "opinions_layer_payload",
    "signatures_layer_payload",
    "disposition_layer_payload",
]


class DossierError(Exception):
    """卷宗组装失败（缺改写留痕等硬性缺失）。"""


class DossierLayer(str, Enum):
    """卷宗六层。顺序即 PRD 8.9 的顺序，不允许增删。"""

    L1 = "L1_事实层快照"
    L2 = "L2_规则版本快照"
    L3 = "L3_依据文档快照"
    L4 = "L4_审查意见层"
    L5 = "L5_人审签核层"
    L6 = "L6_处置与贷后闭环层"


class Completeness(str, Enum):
    """齐全性两档。"""

    COMPLETE = "完整"
    INCOMPLETE = "不完整"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(payload: Any) -> str:
    """层内容哈希。"""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LayerSnapshot:
    """一层的不可变快照。"""

    layer: DossierLayer
    payload: Mapping[str, Any]
    content_hash: str
    written_at: str

    def verify_self(self) -> bool:
        """重算内容哈希，确认这一层没被改过。"""
        return content_hash(self.payload) == self.content_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer.value,
            "payload": dict(self.payload),
            "content_hash": self.content_hash,
            "written_at": self.written_at,
        }


@dataclass(frozen=True)
class DossierVerification:
    """卷宗齐全性校验结果（两档）。"""

    completeness: Completeness
    missing_layers: Tuple[DossierLayer, ...] = ()
    tampered_layers: Tuple[DossierLayer, ...] = ()

    @property
    def ok(self) -> bool:
        return self.completeness is Completeness.COMPLETE

    @property
    def usable_for_audit(self) -> bool:
        """不完整或被改过的卷宗**不可用于监管取证**（PRD 8.9）。"""
        return self.ok and not self.tampered_layers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "completeness": self.completeness.value,
            "missing_layers": [l.value for l in self.missing_layers],
            "tampered_layers": [l.value for l in self.tampered_layers],
            "usable_for_audit": self.usable_for_audit,
        }


class Dossier:
    """案件卷宗。"""

    def __init__(self, case_id: str, *, clock: Optional[Callable[[], str]] = None) -> None:
        self.case_id = case_id
        self._layers: Dict[DossierLayer, LayerSnapshot] = {}
        self._clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))

    # ---------------- 写入 ----------------
    def write(self, layer: DossierLayer, payload: Mapping[str, Any]) -> LayerSnapshot:
        """写入（或覆盖）一层，写入即算内容哈希。

        允许覆盖是因为 L4 / L5 / L6 会在流程推进中多次追加内容
        （如退回后重新提交、贷后预警追加）。**每次写入都留新哈希**，
        旧版本不在卷宗内保留——旧版本在事件日志里（只追加）。

        两条评审修复：
        · payload **深拷贝入库**——调用方事后改动自己的 dict 不会把合法层
          变成「被篡改」（与事件日志同口径）。
        · **空映射拒写**——「写了但什么都没有」的层会让六层齐全性形同虚设
          （空 L3 也算有依据文档层）。层内容至少要有结构；半层（如贷前的 L6
          只有处置决定）由 payload 构造器给出显式空字段表达，不走这里。
        """
        if not isinstance(layer, DossierLayer):
            raise ValueError("未知层：%r" % (layer,))
        if not isinstance(payload, Mapping):
            raise ValueError("层 %s 内容须为映射，收到 %r" % (layer.value, type(payload).__name__))
        if len(payload) == 0:
            raise ValueError("层 %s 内容为空：空层不得计入齐全性（PRD 8.9 缺层即不完整）" % layer.value)
        body = copy.deepcopy(dict(payload))
        snapshot = LayerSnapshot(
            layer=layer,
            payload=body,
            content_hash=content_hash(body),
            written_at=self._clock(),
        )
        self._layers[layer] = snapshot
        return snapshot

    # ---------------- 读取 ----------------
    def read(self, layer: DossierLayer) -> Optional[LayerSnapshot]:
        return self._layers.get(layer)

    def layers(self) -> Tuple[LayerSnapshot, ...]:
        """按 PRD 8.9 顺序返回已写入的层。"""
        return tuple(self._layers[l] for l in DossierLayer if l in self._layers)

    def missing_layers(self) -> Tuple[DossierLayer, ...]:
        """缺哪几层。"""
        return tuple(l for l in DossierLayer if l not in self._layers)

    def has(self, layer: DossierLayer) -> bool:
        return layer in self._layers

    # ---------------- 校验 ----------------
    def verify(self) -> DossierVerification:
        """两档齐全性校验：缺层 → 不完整；层内容被改 → 标记被篡改。"""
        missing = self.missing_layers()
        tampered = tuple(
            l for l in DossierLayer
            if l in self._layers and not self._layers[l].verify_self()
        )
        completeness = (
            Completeness.COMPLETE
            if not missing and not tampered
            else Completeness.INCOMPLETE
        )
        return DossierVerification(
            completeness=completeness,
            missing_layers=missing,
            tampered_layers=tampered,
        )

    # ---------------- 序列化 ----------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "layers": [l.to_dict() for l in self.layers()],
            "verification": self.verify().to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Dossier":
        """从序列化内容还原（哈希按存储值保留，便于校验篡改）。"""
        dossier = cls(data.get("case_id", ""))
        for item in data.get("layers", ()):
            layer = DossierLayer(item["layer"])
            dossier._layers[layer] = LayerSnapshot(
                layer=layer,
                payload=copy.deepcopy(dict(item.get("payload", {}))),
                content_hash=item.get("content_hash", ""),
                written_at=item.get("written_at", ""),
            )
        return dossier


# ---------------------------------------------------------------- 各层内容构造
def facts_layer_payload(factset: Any) -> Dict[str, Any]:
    """L1 事实层快照：取值、来源通道、置信度、冲突裁决结果与裁决人。"""
    fields = []
    for name, f in sorted(getattr(factset, "fields", {}).items()):
        fields.append(
            {
                "field_path": name,
                "value": f.value,
                "confidence": f.confidence.value,
                "source_channels": [c.value for c in f.channels],
                "locator": f.locator,
                "candidates": list(f.candidates),
                "note": f.note,
            }
        )
    pending = []
    for c in getattr(factset, "pending_conflicts", ()) or ():
        pending.append(
            {
                "field": getattr(c, "field", None),
                "kind": getattr(getattr(c, "kind", None), "value", None),
                "candidates": list(getattr(c, "candidates", ()) or ()),
                "adjudicator": getattr(c, "adjudicator", None),
                "adjudicated_at": getattr(c, "adjudicated_at", None),
                "reason": getattr(c, "reason", None),
            }
        )
    return {
        "fields": fields,
        "pending_conflicts": pending,
        "blocked_fields": list(getattr(factset, "blocked_fields", lambda: ())()),
    }


def rules_layer_payload(
    bindings: Iterable[Any],
    unresolved: Iterable[Any] = (),
    hit_paths: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """L2 规则版本快照：绑定版本 + 三键依据 + 命中路径。"""
    items = []
    for b in bindings:
        item = b.to_dict() if hasattr(b, "to_dict") else dict(b)
        items.append(item)
    unres = []
    for u in unresolved:
        item = u.to_dict() if hasattr(u, "to_dict") else dict(u)
        unres.append(item)
    return {
        "rule_binding": items,
        "unresolved": unres,
        "hit_paths": dict(hit_paths or {}),
    }


def documents_layer_payload(citations: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """L3 依据文档快照：chunk_id、原文 quote、来源文号与发布日期。"""
    items = []
    for c in citations:
        items.append(
            {
                "doc_id": c.get("doc_id"),
                "chunk_id": c.get("chunk_id"),
                "source_ref": c.get("source_ref"),
                "quote": c.get("quote"),
                "published_at": c.get("published_at"),
            }
        )
    return {"citations": items}


def opinions_layer_payload(
    opinions: Iterable[Any],
    coverage: Any = None,
    spec_version: str = "",
    amendments: Iterable[Any] = (),
    revision: int = 0,
) -> Dict[str, Any]:
    """L4 审查意见层：结构化意见 + 覆盖度 + 严重度 + 未判定原因码 + AgentSpec 版本。

    **C3 决策**：标记为 ``HUMAN_AMENDED`` 的意见，**必须**有对应的 amendment
    记录（含原始意见、修改后意见与修改理由），否则**组装直接失败**——
    FR-GATE-06 的强制力落在卷宗层，而不是靠签核岗"自觉登记"。

    ``revision`` 是**修订轮次**（评审咨询 C2）：同一案件退回后重新成稿会产出
    新的意见集合，卷宗键因此带修订维度 ``opinion_id@rN``，两轮不会互相覆盖。
    """
    amended_keys = set()
    for a in amendments:
        if hasattr(a, "rule_id"):
            amended_keys.add((a.rule_id, a.rule_version))
        elif isinstance(a, Mapping):
            amended_keys.add((a.get("rule_id"), a.get("rule_version")))

    items = []
    for o in opinions:
        d = o.to_dict() if hasattr(o, "to_dict") else dict(o)
        if d.get("origin") == "human_amended":
            key = (d.get("rule_id"), d.get("rule_version"))
            if key not in amended_keys:
                raise DossierError(
                    "意见 %s 标记为人工改写但缺少对应 amendment 记录"
                    "（FR-GATE-06 双版本与理由缺失，卷宗不得组装）"
                    % d.get("opinion_id")
                )
        d["revision"] = revision
        d["opinion_key"] = "%s@r%d" % (d.get("opinion_id"), revision)
        items.append(d)

    cov: Dict[str, Any] = {}
    if coverage is not None:
        cov = coverage.to_dict() if hasattr(coverage, "to_dict") else dict(coverage)
    return {
        "opinions": items,
        "coverage_summary": cov,
        "producer_spec_version": spec_version,
        "revision": revision,
        "amendment_count": len(list(amendments)),
    }


def signatures_layer_payload(
    gates: Mapping[str, Any],
    stage_trail: Sequence[str] = (),
) -> Dict[str, Any]:
    """L5 人审签核层：三处闸门的签核 / 退回 / 升级 / 改写留痕 + 阶段轨迹。"""
    gate_items = {}
    for gate_id, record in gates.items():
        gate_items[gate_id] = record.to_dict() if hasattr(record, "to_dict") else dict(record)
    return {
        "gates": gate_items,
        "stage_trail": list(stage_trail),
    }


def disposition_layer_payload(
    disposition: Optional[Mapping[str, Any]] = None,
    alerts: Iterable[Mapping[str, Any]] = (),
    verifications: Iterable[Mapping[str, Any]] = (),
    results: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """L6 处置与贷后闭环层：风险处置决定 + 预警 + 核查 + 处置结果。"""
    return {
        "disposition": dict(disposition) if disposition else {},
        "alerts": [dict(a) for a in alerts],
        "verifications": [dict(v) for v in verifications],
        "results": [dict(r) for r in results],
    }