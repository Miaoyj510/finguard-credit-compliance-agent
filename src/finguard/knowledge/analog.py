"""历史审查意见与处罚案例的入库构造（PRD 9.2 / FR-RAG-08）。

入库时**必须**保留：时间戳、**当时适用的规则版本**、处置结果。
调出时**必须**带当时规则版本标签，并标记「参考类比」——
历史意见不是判定依据，它只回答"当年这种情况我们怎么处理的"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .corpus import ChunkLevel, DocChunk, DocType, Sensitivity

__all__ = [
    "OpinionHistoryRecord",
    "PenaltyCaseRecord",
    "build_opinion_history_chunks",
    "build_penalty_case_chunks",
]


@dataclass(frozen=True)
class OpinionHistoryRecord:
    """一条历史审查意见。"""

    opinion_id: str
    rule_id: str
    applied_rule_version: str
    observed_at: str
    disposition: str
    rationale: str
    doc_id: str = "DOC-HIST-REVIEW"
    source_ref: str = "历史审查意见库"
    version: str = "1.0"
    title: str = "历史审查意见"
    acl: Tuple[str, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL


@dataclass(frozen=True)
class PenaltyCaseRecord:
    """一条处罚案例。"""

    case_id: str
    cause: str
    circumstances: str
    penalty: str
    source: str
    doc_id: str = "DOC-HIST-PENALTY"
    source_ref: str = "处罚案例库"
    version: str = "1.0"
    title: str = "处罚案例"
    acl: Tuple[str, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL


def build_opinion_history_chunks(
    records: Sequence[OpinionHistoryRecord],
    *,
    ingested_at: Optional[str] = None,
) -> Tuple[DocChunk, ...]:
    """历史意见：一条意见一卡（PRD 9.2）。"""
    chunks = []
    for record in records:
        chunks.append(
            DocChunk(
                doc_id=record.doc_id,
                chunk_id="%s-%s" % (record.doc_id, record.opinion_id),
                doc_type=DocType.OPINION_HISTORY,
                chunk_level=ChunkLevel.OPINION,
                title=record.title,
                source_ref="%s %s" % (record.source_ref, record.opinion_id),
                quote="%s %s" % (record.rationale, record.disposition),
                version=record.version,
                parent_path=(record.opinion_id,),
                acl=record.acl,
                sensitivity=record.sensitivity,
                ingested_at=ingested_at,
                is_reference_only=True,
                applied_rule_id=record.rule_id,
                applied_rule_version=record.applied_rule_version,
                observed_at=record.observed_at,
                disposition=record.disposition,
            )
        )
    return tuple(chunks)


def build_penalty_case_chunks(
    records: Sequence[PenaltyCaseRecord],
    *,
    ingested_at: Optional[str] = None,
) -> Tuple[DocChunk, ...]:
    """处罚案例：案例段落级（PRD 9.2）。"""
    chunks = []
    for record in records:
        chunks.append(
            DocChunk(
                doc_id=record.doc_id,
                chunk_id="%s-%s" % (record.doc_id, record.case_id),
                doc_type=DocType.PENALTY_CASE,
                chunk_level=ChunkLevel.CASE,
                title=record.title,
                source_ref="%s %s" % (record.source_ref, record.case_id),
                quote="案由：%s 情节：%s 处罚结果：%s 来源：%s"
                      % (record.cause, record.circumstances, record.penalty, record.source),
                version=record.version,
                parent_path=(record.case_id,),
                acl=record.acl,
                sensitivity=record.sensitivity,
                ingested_at=ingested_at,
                is_reference_only=True,
            )
        )
    return tuple(chunks)