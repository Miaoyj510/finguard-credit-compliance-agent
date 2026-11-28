"""检索层：**只定位依据与出处，不产生判定**（PRD 7.2 / FR-RAG-06 / 9.2 切片纪律）。

检索结果的字段**只有** PRD 9.3 规定的那几个：

    doc_id / chunk_id / source_ref / version / quote

外加定位信息与检索元数据（score / chunk_level / parent_label / is_reference_only）。
**没有结论字段**——不返回命中、不返回严重度、不返回"是否违规"。
判定只发生在规则引擎里（切片 2），检索层**不参与**。

三条安全约束：

1. **ACL 过滤**（PRD 11.4 S-06 越权检索）：返回前按调用方角色过滤切片 acl。
2. **范围过滤**：按产品类型与机构层级过滤（切片自身带 scope）。
3. **参考类比标记**（FR-RAG-08）：历史意见与处罚案例的检索结果**必须**带
   ``is_reference_only=True`` 与**当时适用的规则版本**，只作类比，不作判定依据。

打分用**确定性关键词重叠**（占位实现）。接口与向量检索一致——
切片 9 接 pgvector 时只换 ``scorer``，上层契约不变。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .corpus import ChunkLevel, DocChunk, DocStore, DocType, Sensitivity
from .permissions import Role, can_read_documents

__all__ = [
    "RetrievalHit",
    "DocRetriever",
    "AnalogRetriever",
    "RETRIEVAL_FIELDS",
    "keyword_score",
]

#: 检索结果的**字段白名单**——PRD 9.3 规定的引用字段 + 定位 + 检索元数据。
#: 白名单之外的名字一律不许出现（尤其结论类字段）。
RETRIEVAL_FIELDS: Tuple[str, ...] = (
    "doc_id", "chunk_id", "source_ref", "version", "quote",
    "chunk_level", "doc_type", "parent_label", "score", "is_reference_only",
    "applied_rule_id", "applied_rule_version", "observed_at", "disposition",
    # 评审确认项 2 落地：PRD 9.3 的 content_hash 随命中返回——
    # 意见引用与卷宗 L3 的防篡改复验依赖它（拿到引用即可复核出处未被改）。
    "content_hash",
)

_SPLIT_RE = re.compile(r"[\s,，。；、：:()（）【】\[\]“”\"'｜|/\\·]+")


def _bigrams(text: str) -> List[str]:
    """中文取 2-gram、英文与数字取词——确定性的轻量分词。"""
    out: List[str] = []
    for token in _SPLIT_RE.split(text or ""):
        if not token:
            continue
        if len(token) == 1:
            out.append(token)
            continue
        if re.fullmatch(r"[A-Za-z0-9_.@\-]+", token):
            out.append(token.lower())
            continue
        for i in range(len(token) - 1):
            out.append(token[i:i + 2])
    return out


def keyword_score(query: str, quote: str) -> float:
    """确定性关键词重叠打分（占位实现，切片 9 换向量检索）。"""
    q = set(_bigrams(query))
    if not q:
        return 0.0
    d = set(_bigrams(quote))
    if not d:
        return 0.0
    hit = len(q & d)
    if not hit:
        return 0.0
    return round(hit / (len(q) ** 0.5), 6)


@dataclass(frozen=True)
class RetrievalHit:
    """一条检索结果。

    ⚠ 这里**没有** verdict / severity / hit 一类字段——
    检索层只负责"找得到"，不负责"判得了"（PRD 9.2 切片纪律）。
    """

    doc_id: str
    chunk_id: str
    source_ref: str
    version: str
    quote: str
    chunk_level: str
    doc_type: str
    parent_label: str
    score: float
    is_reference_only: bool = False
    applied_rule_id: str = ""
    applied_rule_version: str = ""
    observed_at: str = ""
    disposition: str = ""
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "source_ref": self.source_ref,
            "version": self.version,
            "quote": self.quote,
            "chunk_level": self.chunk_level,
            "doc_type": self.doc_type,
            "parent_label": self.parent_label,
            "score": self.score,
            "is_reference_only": self.is_reference_only,
            "applied_rule_id": self.applied_rule_id,
            "applied_rule_version": self.applied_rule_version,
            "observed_at": self.observed_at,
            "disposition": self.disposition,
            "content_hash": self.content_hash,
        }


def _to_hit(chunk: DocChunk, score: float) -> RetrievalHit:
    return RetrievalHit(
        doc_id=chunk.doc_id,
        chunk_id=chunk.chunk_id,
        source_ref=chunk.source_ref,
        version=chunk.version,
        quote=chunk.quote,
        chunk_level=chunk.chunk_level.value,
        doc_type=chunk.doc_type.value,
        parent_label=chunk.parent_label,
        score=score,
        is_reference_only=chunk.is_reference_only,
        applied_rule_id=chunk.applied_rule_id,
        applied_rule_version=chunk.applied_rule_version,
        observed_at=chunk.observed_at or "",
        disposition=chunk.disposition,
        content_hash=chunk.content_hash,
    )


class DocRetriever:
    """文档检索。"""

    def __init__(
        self,
        store: DocStore,
        *,
        scorer: Optional[Callable[[str, str], float]] = None,
    ) -> None:
        self.store = store
        self._scorer = scorer or keyword_score

    # ---------------- 过滤 ----------------
    def _visible(
        self,
        chunk: DocChunk,
        *,
        role: Optional[str],
        product_type: Optional[str],
        org_level: Optional[str],
    ) -> bool:
        if role is not None:
            if not can_read_documents(role, chunk.sensitivity):
                return False
            acl = chunk.acl
            if acl and ("role:%s" % role) not in acl:
                return False
            # 评审修复 R-G3：PRD 9.4 文档库列 L5 授信审查岗 = 「读生效版本」。
            # 失效制度对授信岗可见会误导信贷决策；「生效」口径与规则库一致
            # （effective_to 为空 = 长期生效）。
            if role == Role.L5.value and chunk.effective_to is not None:
                return False
        if product_type is not None and chunk.product_scope:
            if "*" not in chunk.product_scope and product_type not in chunk.product_scope:
                return False
        if org_level is not None and chunk.org_scope:
            if "*" not in chunk.org_scope and org_level not in chunk.org_scope:
                return False
        return True

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        role: Optional[str] = None,
        product_type: Optional[str] = None,
        org_level: Optional[str] = None,
        doc_types: Optional[Sequence[DocType]] = None,
    ) -> Tuple[RetrievalHit, ...]:
        """检索并返回**引用**。分数为 0 的不返回（召回即噪声）。"""
        scored: List[Tuple[float, DocChunk]] = []
        for chunk in self.store.chunks():
            if doc_types is not None and chunk.doc_type not in doc_types:
                continue
            if chunk.is_reference_only:
                # 历史意见与处罚案例**只作参考类比**，必须走 AnalogRetriever，
                # 不能混进"找制度依据"的检索里——两者的用途与可信度不同。
                continue
            if not self._visible(chunk, role=role, product_type=product_type, org_level=org_level):
                continue
            score = self._scorer(query, chunk.quote)
            if score <= 0:
                continue
            scored.append((score, chunk))
        scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
        return tuple(_to_hit(chunk, score) for score, chunk in scored[:top_k])


class AnalogRetriever:
    """历史审查意见与处罚案例检索（FR-POST / FR-RAG-08）。

    与制度检索**分开**：历史语料**只作类比参考**，不得作为命中依据。
    因此结果强制 ``is_reference_only=True``，且**必须**带**当时适用的规则版本**——
    没有版本标签的历史意见没有参考价值（它无法说明"当年是按哪版判的"）。
    """

    ANALOG_TYPES = (DocType.OPINION_HISTORY, DocType.PENALTY_CASE)

    def __init__(
        self,
        store: DocStore,
        *,
        scorer: Optional[Callable[[str, str], float]] = None,
    ) -> None:
        self.store = store
        self._scorer = scorer or keyword_score

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        role: Optional[str] = None,
    ) -> Tuple[RetrievalHit, ...]:
        scored: List[Tuple[float, DocChunk]] = []
        for chunk in self.store.chunks():
            if chunk.doc_type not in self.ANALOG_TYPES:
                continue
            if role is not None:
                if not can_read_documents(role, chunk.sensitivity):
                    continue
                if chunk.acl and ("role:%s" % role) not in chunk.acl:
                    continue
            score = self._scorer(query, chunk.quote)
            if score <= 0:
                continue
            scored.append((score, chunk))
        scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
        hits = []
        for score, chunk in scored[:top_k]:
            hit = _to_hit(chunk, score)
            if not hit.applied_rule_version:
                # 没有当时规则版本的历史意见**不作为参考**返回——
                # 否则用户会拿一条无法定位口径的旧意见当依据。
                continue
            hits.append(
                RetrievalHit(
                    doc_id=hit.doc_id, chunk_id=hit.chunk_id, source_ref=hit.source_ref,
                    version=hit.version, quote=hit.quote, chunk_level=hit.chunk_level,
                    doc_type=hit.doc_type, parent_label=hit.parent_label, score=hit.score,
                    is_reference_only=True,
                    applied_rule_id=hit.applied_rule_id,
                    applied_rule_version=hit.applied_rule_version,
                    observed_at=hit.observed_at, disposition=hit.disposition,
                    content_hash=hit.content_hash,
                )
            )
        return tuple(hits)