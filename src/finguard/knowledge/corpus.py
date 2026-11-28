"""文档库：切片容器与物理隔离的文档存储（PRD 7.2 / 9.2 / 9.3）。

**分库是硬要求**（FR-RAG-02）：规则库（``finguard.rules.registry.RuleStore``）存
可判定表达式与元数据，文档库（本模块）存非结构化文本，两者 schema **不互通**，
且**判定路径不接触向量库**——规则引擎所在模块不 import 本包（有断言钉住）。

metadata schema 照 PRD 9.3 落地，字段名保持一致，便于与文档库后台对齐。

切片纪律（PRD 9.2）：**切片只负责"找得到"，不负责"判得了"**——
任何切片都不得携带结论标签（命中 / 违规 / 合规），避免检索层越权形成判定。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "DocType",
    "ChunkLevel",
    "Sensitivity",
    "DocChunk",
    "DocStore",
    "CONCLUSION_FIELD_MARKERS",
    "assert_no_conclusion_fields",
]


class DocType(str, Enum):
    """文档类型（PRD 9.3 ``doc_type``）。"""

    INTERNAL_POLICY = "internal_policy"     # 内部制度与操作规程
    REGULATOR_POLICY = "regulator_policy"   # 监管政策与口径
    RULE = "rule"                           # 规则（文档库侧的说明卡，与规则库分属两库）
    OPINION_HISTORY = "opinion_history"     # 历史审查意见
    PENALTY_CASE = "penalty_case"           # 处罚案例
    ELEMENT_CARD = "element_card"           # 反洗钱/内控要素清单（一要素一卡）


class ChunkLevel(str, Enum):
    """切片粒度（PRD 9.3 ``chunk_level``）。"""

    CLAUSE = "clause"        # 制度条款级
    PARAGRAPH = "paragraph"  # 监管政策段落级
    ELEMENT = "element"      # 要素级
    OPINION = "opinion"      # 一条意见一卡
    CASE = "case"            # 案例段落级


class Sensitivity(str, Enum):
    """敏感级别（PRD 9.3 ``sensitivity``）。"""

    INTERNAL = "internal"
    RESTRICTED = "restricted"


#: 结论类字段名——**切片里出现这些就是越权**（PRD 9.2 切片纪律）
CONCLUSION_FIELD_MARKERS: Tuple[str, ...] = (
    "verdict", "hit", "severity", "compliant", "violation", "illegal",
    "结论", "命中", "违规", "合规", "处罚", "严重度",
)


def assert_no_conclusion_fields(payload: Mapping[str, Any], where: str = "") -> None:
    """检查一个 payload 里没有结论类字段名。

    这是"检索层不得形成判定"的**结构性**保证：
    不是靠约定不写，而是写了就报错。
    """
    for key in payload:
        lowered = str(key).lower()
        for marker in CONCLUSION_FIELD_MARKERS:
            if marker.lower() in lowered:
                raise ValueError(
                    "%s 携带结论类字段 %r——切片只负责找得到，不负责判得了（PRD 9.2）"
                    % (where or "切片", key)
                )


@dataclass(frozen=True)
class DocChunk:
    """一个文档切片。"""

    doc_id: str
    chunk_id: str
    doc_type: DocType
    chunk_level: ChunkLevel
    title: str
    source_ref: str
    quote: str
    version: str
    issuer: str = ""
    publish_date: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    parent_path: Tuple[str, ...] = ()
    locator: Mapping[str, Any] = field(default_factory=dict)
    product_scope: Tuple[str, ...] = ()
    org_scope: Tuple[str, ...] = ()
    acl: Tuple[str, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    ingested_at: Optional[str] = None
    content_hash: str = ""
    is_reference_only: bool = False
    # 要素级专用：要素编号、要素名称、判定口径、所需证据字段（PRD 9.2）
    element_id: str = ""
    element_name: str = ""
    criteria: str = ""
    required_evidence_fields: Tuple[str, ...] = ()
    # 历史意见/案例专用：当时适用的规则版本与处置结果（PRD 9.2 / FR-RAG-08）
    applied_rule_id: str = ""
    applied_rule_version: str = ""
    observed_at: Optional[str] = None
    disposition: str = ""

    def __post_init__(self) -> None:
        body = {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "quote": self.quote,
            "version": self.version,
            "chunk_level": self.chunk_level.value,
        }
        object.__setattr__(self, "content_hash", "sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest())
        object.__setattr__(self, "locator", MappingProxyType(dict(self.locator)))

    @property
    def parent_label(self) -> str:
        """章条路径的可读标签（如「第三章 / 第十一条」）。"""
        return " / ".join(self.parent_path)

    def metadata(self) -> Dict[str, Any]:
        """PRD 9.3 的 metadata schema。"""
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type.value,
            "title": self.title,
            "source_ref": self.source_ref,
            "issuer": self.issuer,
            "publish_date": self.publish_date,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "version": self.version,
            "chunk_id": self.chunk_id,
            "chunk_level": self.chunk_level.value,
            "parent_path": list(self.parent_path),
            "locator": dict(self.locator),
            "product_scope": list(self.product_scope),
            "org_scope": list(self.org_scope),
            "acl": list(self.acl),
            "sensitivity": self.sensitivity.value,
            "ingested_at": self.ingested_at,
            "content_hash": self.content_hash,
            "is_reference_only": self.is_reference_only,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = self.metadata()
        d.update({
            "quote": self.quote,
            "parent_label": self.parent_label,
            "element_id": self.element_id,
            "element_name": self.element_name,
            "criteria": self.criteria,
            "required_evidence_fields": list(self.required_evidence_fields),
            "applied_rule_id": self.applied_rule_id,
            "applied_rule_version": self.applied_rule_version,
            "observed_at": self.observed_at,
            "disposition": self.disposition,
        })
        return d


class DocStore:
    """文档库。

    与规则库**物理分离**：独立的模块、独立的 schema、独立的索引。
    这里不提供任何"判定"能力——只提供切片存取与检索入口。
    """

    def __init__(self, chunks: Iterable[DocChunk] = ()) -> None:
        self._chunks: Dict[str, DocChunk] = {}
        for chunk in chunks:
            self.add(chunk)

    #: 历史意见与处罚案例**只能**以「参考类比」身份入库（FR-RAG-08）。
    #: 评审修复 R-G2：禁旗原先靠入库方自觉——关掉旗的历史意见会被
    #: DocRetriever 当制度依据返回，「历史不作判定依据」就成了一纸声明。
    REFERENCE_ONLY_TYPES: Tuple["DocType", ...] = (
        DocType.OPINION_HISTORY,
        DocType.PENALTY_CASE,
    )

    def add(self, chunk: DocChunk) -> DocChunk:
        assert_no_conclusion_fields(chunk.metadata(), "文档切片 %s" % chunk.chunk_id)
        if chunk.doc_type in self.REFERENCE_ONLY_TYPES and not chunk.is_reference_only:
            raise ValueError(
                "文档切片 %s 属 %s：历史意见与处罚案例只能以参考类比入库"
                "（is_reference_only 必须为真，FR-RAG-08）"
                % (chunk.chunk_id, chunk.doc_type.value)
            )
        if chunk.chunk_id in self._chunks:
            raise ValueError("切片重复：%s" % chunk.chunk_id)
        self._chunks[chunk.chunk_id] = chunk
        return chunk

    def get(self, chunk_id: str) -> DocChunk:
        return self._chunks[chunk_id]

    def chunks(self) -> Tuple[DocChunk, ...]:
        return tuple(self._chunks.values())

    def by_doc(self, doc_id: str) -> Tuple[DocChunk, ...]:
        return tuple(c for c in self._chunks.values() if c.doc_id == doc_id)

    def by_level(self, level: ChunkLevel) -> Tuple[DocChunk, ...]:
        return tuple(c for c in self._chunks.values() if c.chunk_level is level)

    def by_doc_type(self, doc_type: DocType) -> Tuple[DocChunk, ...]:
        return tuple(c for c in self._chunks.values() if c.doc_type is doc_type)

    def __len__(self) -> int:
        return len(self._chunks)