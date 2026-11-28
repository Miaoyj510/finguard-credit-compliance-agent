"""双粒度切片策略（PRD 9.2、FR-RAG-07）。

两类切片，对应两类来源：

    **条款级**（内部制度与操作规程）300—800 字，保留 章号 / 条号 / 标题 /
    发布文号 / 生效日，**与相邻条款重叠 1 句**——跨条引用时上下文不丢。

    **要素级**（反洗钱 / 内控要素清单）80—300 字，一要素一卡，保留
    要素编号 / 要素名称 / 判定口径 / 所需证据字段。

⚠ 切片只负责"找得到"，不负责"判得了"——切片里**不得**出现结论标签。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .corpus import ChunkLevel, DocChunk, DocType, Sensitivity

__all__ = [
    "PolicyDocument",
    "ElementSpec",
    "CLAUSE_SIZE_BOUNDS",
    "ELEMENT_SIZE_BOUNDS",
    "chunk_policy_clauses",
    "chunk_elements",
    "split_sentences",
]

#: 条款级切片大小区间（自建评测集口径 [D]，PRD 9.2）
CLAUSE_SIZE_BOUNDS = (300, 800)
#: 要素级切片大小区间（自建评测集口径 [D]，PRD 9.2）
ELEMENT_SIZE_BOUNDS = (80, 300)

# 条款行：「第X条」+ 正文首段（真实制度通常写在同一行）
_CLAUSE_RE = re.compile(r"^(第[一二三四五六七八九十百零]+条)\s*(.*)$")
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零]+章\s*(.*)$")
_SENT_END = "。！？；"


def split_sentences(text: str) -> List[str]:
    """按中文句末标点切句（保留标点）。"""
    out: List[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


@dataclass(frozen=True)
class PolicyDocument:
    """待切片的制度文件（演示用合成文档）。"""

    doc_id: str
    title: str
    source_ref: str
    version: str
    body: str
    issuer: str = ""
    publish_date: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    product_scope: Tuple[str, ...] = ()
    org_scope: Tuple[str, ...] = ()
    acl: Tuple[str, ...] = ()
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    ingested_at: Optional[str] = None


@dataclass(frozen=True)
class ElementSpec:
    """待切片的要素卡（反洗钱 / 内控要素清单）。"""

    element_id: str
    element_name: str
    criteria: str
    required_evidence_fields: Tuple[str, ...]
    text: str


def chunk_policy_clauses(
    doc: PolicyDocument,
    *,
    overlap_sentences: int = 1,
) -> Tuple[DocChunk, ...]:
    """把制度文件切成**条款级**切片。

    规则：
      · 以"第X条"为切分点；章号由最近的"第X章"确定
      · 每个切片保留 **章号 + 条号 + 标题 + 发布文号 + 生效日**
      · 与**相邻条款重叠 N 句**（默认 1 句）：下一片的开头接上一片的结尾
    """
    lines = doc.body.split("\n")
    marks: List[Tuple[int, str, str]] = []      # (行号, 条号全文, 条标题)
    chapter = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        chapter_match = _CHAPTER_RE.match(stripped)
        if chapter_match:
            chapter = stripped
            continue
        clause_match = _CLAUSE_RE.match(stripped)
        if clause_match:
            marks.append(
                (index, clause_match.group(1).strip(), clause_match.group(2).strip())
            )

    if not marks:
        raise ValueError("制度文件 %s 里没有找到「第X条」标记，无法切条款级" % doc.doc_id)

    chunks: List[DocChunk] = []
    bodies: List[str] = []
    titles: List[str] = []
    chapters: List[str] = []
    for pos, (line_no, header, first_paragraph) in enumerate(marks):
        end = marks[pos + 1][0] if pos + 1 < len(marks) else len(lines)
        body_lines = [l.strip() for l in lines[line_no + 1:end] if l.strip()]
        # 条款首段（与「第X条」同一行）+ 后续行，合起来才是这条的正文
        bodies.append(" ".join(([first_paragraph] if first_paragraph else []) + body_lines))
        chapters.append(chapter)

    for pos, (line_no, header, _first) in enumerate(marks):
        text = bodies[pos]
        parent: List[str] = []
        if chapters[pos]:
            parent.append(chapters[pos])
        parent.append(header)
        if pos > 0:
            # 重叠方向：**后一块的开头**接**前一块的结尾**（PRD 9.2「与相邻条款重叠 1 句」）
            prev_tail = split_sentences(bodies[pos - 1])
            carry = "".join(prev_tail[-overlap_sentences:]) if prev_tail else ""
            text = (carry + text) if carry else text
        chunks.append(
            DocChunk(
                doc_id=doc.doc_id,
                chunk_id="%s-C%03d" % (doc.doc_id, pos + 1),
                doc_type=DocType.INTERNAL_POLICY,
                chunk_level=ChunkLevel.CLAUSE,
                title=doc.title,
                source_ref="%s %s" % (doc.source_ref, header),
                # quote 只放**原文片段**（PRD 9.3 的 quote 语义）；
                # 条号在 source_ref 与 parent_path 里，不混进引用正文。
                quote=text,
                version=doc.version,
                issuer=doc.issuer,
                publish_date=doc.publish_date,
                effective_from=doc.effective_from,
                effective_to=doc.effective_to,
                parent_path=tuple(parent),
                product_scope=doc.product_scope,
                org_scope=doc.org_scope,
                acl=doc.acl,
                sensitivity=doc.sensitivity,
                ingested_at=doc.ingested_at,
            )
        )
    return tuple(chunks)


def chunk_elements(
    doc_id: str,
    title: str,
    source_ref: str,
    version: str,
    specs: Sequence[ElementSpec],
    *,
    acl: Tuple[str, ...] = (),
    product_scope: Tuple[str, ...] = (),
    org_scope: Tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    ingested_at: Optional[str] = None,
) -> Tuple[DocChunk, ...]:
    """把要素清单切成**要素级**切片（一要素一卡）。

    评审修复 R-G4：80—300 字是自建口径 [D]，原先只在测试里验了演示数据，
    入库路径不设防——过短/过长的要素卡会静默进库，评测口径失去结构性。
    """
    chunks: List[DocChunk] = []
    for spec in specs:
        size = len(spec.text)
        if not ELEMENT_SIZE_BOUNDS[0] <= size <= ELEMENT_SIZE_BOUNDS[1]:
            raise ValueError(
                "要素卡 %s 长度 %d 超出 %d—%d 字口径（PRD 9.2 [D]）：%r"
                % (spec.element_id, size, ELEMENT_SIZE_BOUNDS[0],
                   ELEMENT_SIZE_BOUNDS[1], spec.text[:24])
            )
        chunks.append(
            DocChunk(
                doc_id=doc_id,
                chunk_id="%s-%s" % (doc_id, spec.element_id),
                doc_type=DocType.ELEMENT_CARD,
                chunk_level=ChunkLevel.ELEMENT,
                title="%s · %s" % (title, spec.element_name),
                source_ref="%s %s" % (source_ref, spec.element_id),
                quote=spec.text,
                version=version,
                issuer="",
                parent_path=(spec.element_name,),
                product_scope=product_scope,
                org_scope=org_scope,
                acl=acl,
                sensitivity=sensitivity,
                ingested_at=ingested_at,
                element_id=spec.element_id,
                element_name=spec.element_name,
                criteria=spec.criteria,
                required_evidence_fields=spec.required_evidence_fields,
            )
        )
    return tuple(chunks)