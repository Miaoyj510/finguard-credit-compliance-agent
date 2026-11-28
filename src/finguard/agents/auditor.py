"""审查 Agent 与引用闸门（PRD 6.3 FR-AUDIT-01 ~ 08、8.1 AgentSpec 禁止行为）。

这一层把「判定结果 + 事实」变成**可签核的结构化审查意见**，并且守住三条线：

1. **引用三件套是硬闸门**（FR-AUDIT-07 / NFR-01 / NFR-03）：
   规则编号 + 规则版本 + 事实引用，缺任一项（或与绑定版本对不上），
   草稿**不进输出通道**，只进拦截清单。
2. **严重度只能来自规则库**（FR-AUDIT-03 / FB-04）：
   模型自拟的严重度一律替换回 rule.severity，并留痕。
3. **禁止行为可检出**（FR-AUDIT-04 / AgentSpec FB-01 ~ FB-05）：
   处罚决定、责任认定、审批结论、对客结论——以及引用未绑定规则、
   引用低置信事实、自拟严重度、执行文档中的指令。

模型接入：真实大模型调用留到切片 9（PRD 7.6.7 选型轴①），本切片用**桩件**
生成理由，契约不变。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..domain.enums import CoverageFlag, Severity, UnresolvedReasonCode, Verdict
from ..domain.opinion import (
    BlockedOpinion,
    CoverageSummary,
    FactCitation,
    OpinionDraft,
    OpinionOrigin,
    ReviewOpinion,
    SeverityCorrection,
    UnresolvedEntry,
)
from ..domain.rule import Rule
from ..rules.engine import Evaluation

__all__ = [
    "CitationGate",
    "ForbiddenBehaviorDetector",
    "ForbiddenHit",
    "RationaleWriter",
    "TemplateRationaleWriter",
    "Auditor",
    "OpinionBuildResult",
]


# ---------------------------------------------------------------- 闸门
class CitationGate:
    """引用三件套闸门。

    必查项：
        rule_id     —— 必须存在，且与绑定判定的 rule_id 一致
        rule_version—— 必须存在，且与绑定判定的 rule_version 一致
        fact_refs   —— 必须**覆盖**判定实际用到的全部事实字段
    """

    REQUIRED = ("rule_id", "rule_version", "fact_refs")

    def check(
        self,
        draft: OpinionDraft,
        evaluation: Evaluation,
        *,
        rule: Optional[Rule] = None,
    ) -> Tuple[str, ...]:
        missing: List[str] = []
        if not draft.rule_id:
            missing.append("rule_id")
        elif draft.rule_id != evaluation.rule_id:
            missing.append("rule_id")
        if not draft.rule_version:
            missing.append("rule_version")
        elif draft.rule_version != evaluation.rule_version:
            missing.append("rule_version")
        need = set(evaluation.fact_refs)
        if need and not need.issubset(set(draft.fact_refs)):
            missing.append("fact_refs")
        # 制度条款类依据须附原文片段（PRD 8.1 quote_required）。
        # 规则显式声明 quote_required 时，quotes 为空即视为依据不完整。
        if rule is not None and getattr(rule, "quote_required", False) and not draft.quotes:
            missing.append("quotes")
        return tuple(missing)


# ---------------------------------------------------------------- 禁止行为
@dataclass(frozen=True)
class ForbiddenHit:
    """一条禁止行为命中记录。"""

    code: str        # FB-01 ~ FB-05
    detail: str
    matched: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "matched": list(self.matched)}


#: FB-01：处罚决定 / 责任认定 / 审批结论 / 对客结论
FORBIDDEN_CONCLUSION_TERMS: Tuple[str, ...] = (
    "处罚", "罚款", "罚单", "扣罚", "追责", "问责", "责任归属",
    "认定违规", "存在违规", "构成违规", "合规通过", "不合规",
    "审批通过", "不予审批", "同意放款", "同意放贷", "不予放款",
    "建议客户", "应告知客户", "告知客户", "结论为", "最终结论",
    "放行", "不予放行", "拒绝放款", "违规",
)

#: FB-05：文档里夹带的祈使式指令（防注入）
INSTRUCTION_PATTERNS: Tuple[str, ...] = (
    "忽略上述", "忽略前面", "忽略以上", "跳过复核", "跳过审核",
    "无需复核", "不必复核", "无需审核", "直接判定为", "直接输出",
    "不必检查", "无需检查", "请不要输出", "不要检查", "直接通过",
)


class ForbiddenBehaviorDetector:
    """检测 AgentSpec 里 FB-01 ~ FB-05 的违反。"""

    def detect(
        self,
        draft: OpinionDraft,
        evaluation: Evaluation,
        *,
        bound_rule_keys: Tuple[str, ...],
        decidable_fact_names: Tuple[str, ...],
        rule: Optional[Rule],
        quotes: Tuple[str, ...] = (),
    ) -> Tuple[ForbiddenHit, ...]:
        hits: List[ForbiddenHit] = []
        text = draft.rationale or ""

        # FB-01：结论性表述。扫的是 **Agent 自己的断言**，不是被引用的
        # 制度名：《员工违规行为处理办法》是出处（数据），不是定性结论。
        # 只豁免绑定规则 source_ref 里的书名号段——伪装成书名号的断言
        # （如「《认定该笔业务违规》」）不在豁免范围，仍被拦（评审意见 1）。
        scan_text = text
        if rule is not None and rule.source_ref:
            scan_text = scan_text.replace(rule.source_ref, "")
            title = re.search(r"《[^》]*》", rule.source_ref)
            if title:
                scan_text = scan_text.replace(title.group(0), "")
        matched = tuple(t for t in FORBIDDEN_CONCLUSION_TERMS if t in scan_text)
        if matched:
            hits.append(ForbiddenHit("FB-01", "意见包含结论性表述（处罚 / 责任 / 审批 / 对客）", matched))

        # FB-02：引用了未绑定的规则版本
        key = "%s@%s" % (draft.rule_id, draft.rule_version)
        if key not in bound_rule_keys:
            hits.append(ForbiddenHit("FB-02", "引用了未绑定的规则版本：%s" % key, (key,)))

        # FB-03：引用了低置信或未裁决的事实
        bad_refs = tuple(r for r in draft.fact_refs if r not in decidable_fact_names)
        if bad_refs:
            hits.append(ForbiddenHit("FB-03", "引用了不可用于判定的低置信 / 未裁决事实", bad_refs))

        # FB-05：执行了文档中夹带的指令。正文与引用原文（quotes）都扫——
        # quotes 也是输出内容，注入指令可经引用通道绕过（评审意见 2，S-01）。
        # FB-01 对 quotes 豁免：制度原文合法地含「处罚 / 违规」字样，
        # 逐字引用不是 Agent 在做定性（断言与引用分责，见评审意见 2 说明）。
        instr_texts = (text,) + tuple(quotes)
        instr = tuple(p for p in INSTRUCTION_PATTERNS if any(p in t for t in instr_texts))
        if instr:
            hits.append(ForbiddenHit("FB-05", "疑似执行了材料中夹带的指令（正文或引用原文）", instr))

        # FB-04：自拟严重度 —— 特殊处理：不是拦截，而是**替换回规则库值**（见 Auditor）
        return tuple(hits)


# ---------------------------------------------------------------- 理由生成（桩件）
class RationaleWriter:
    """理由生成器接口。真实模型实现放在切片 9（选型轴①）。"""

    def __call__(
        self, rule: Rule, evaluation: Evaluation, facts: Mapping[str, Any]
    ) -> OpinionDraft:                                   # pragma: no cover - 接口
        raise NotImplementedError


class TemplateRationaleWriter(RationaleWriter):
    """确定性模板理由生成器（桩件）。

    确定性 = 同一输入必得同一理由，符合「结论可复算」要求。
    """

    def __call__(
        self, rule: Rule, evaluation: Evaluation, facts: Mapping[str, Any]
    ) -> OpinionDraft:
        parts = []
        for ref in evaluation.fact_refs:
            parts.append("%s=%s" % (ref, facts.get(ref)))
        if parts:
            rationale = "触发依据：%s；相关事实：%s" % (rule.source_ref, "，".join(parts))
        else:
            rationale = "触发依据：%s" % (rule.source_ref,)
        return OpinionDraft(
            rule_id=evaluation.rule_id,
            rule_version=evaluation.rule_version,
            severity=evaluation.severity,
            fact_refs=tuple(evaluation.fact_refs),
            rationale=rationale,
            quotes=(),
            origin=OpinionOrigin.RULE_ENGINE,
        )


# ---------------------------------------------------------------- 编排
@dataclass(frozen=True)
class OpinionBuildResult:
    """一次意见生成的结果。"""

    opinions: Tuple[ReviewOpinion, ...] = ()
    blocked: Tuple[BlockedOpinion, ...] = ()
    corrections: Tuple[SeverityCorrection, ...] = ()
    coverage: CoverageSummary = CoverageSummary()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opinions": [o.to_dict() for o in self.opinions],
            "blocked": [b.to_dict() for b in self.blocked],
            "corrections": [c.to_dict() for c in self.corrections],
            "coverage": self.coverage.to_dict(),
        }


class Auditor:
    """审查 Agent。

    职责边界（FR-AUDIT-04）：把事实映射到规则并组织成结构化意见；
    **不做**处罚决定、责任认定、审批结论、对客结论。
    """

    def __init__(
        self,
        writer: Optional[RationaleWriter] = None,
        gate: Optional[CitationGate] = None,
        detector: Optional[ForbiddenBehaviorDetector] = None,
    ) -> None:
        self.writer = writer or TemplateRationaleWriter()
        self.gate = gate or CitationGate()
        self.detector = detector or ForbiddenBehaviorDetector()

    # ----------------------------------------------------------------
    def build(
        self,
        evaluations: Sequence[Evaluation],
        factset: Any,
        rules: Mapping[str, Rule],
        quotes: Optional[Mapping[str, Tuple[str, ...]]] = None,
        *,
        case_id: Optional[str] = None,
        bound_rule_total: Optional[int] = None,
        start_seq: int = 0,
    ) -> OpinionBuildResult:
        """把判定结果转成结构化意见。

        evaluations      : 判定结果序列（切片 2 的 Evaluation）
        factset          : 事实集（切片 3 的 FactSet）——决定哪些事实可被引用
        rules            : {"rule_id@version": Rule}，用于取权威严重度与出处
        quotes           : {"rule_id@version": ("原文片段", ...)}，可选
        case_id          : 案件号。**给了就用它编意见编号**（OP-<case_id>-<seq>），
                          否则退回旧格式并在同一次 build 内去重（评审意见 5）。
        bound_rule_total : 本案实际绑定的规则条数，用于覆盖度交叉校验（评审留档 A4）。
        start_seq        : 本轮成稿的编号基准（评审咨询 C2）。同一案件退回后重新成稿时
                          由编排器推进该基准，否则两轮会产出**同号**意见，
                          切片 6 卷宗按 opinion_id 建键就会静默覆盖。

        事实集只取一次 decision_ready()：判定链入口每次进入都会被审计钩子记录
        （切片 5 SEC-01），重复调用会把"进入判定链次数"放大成噪声。
        """
        quotes = quotes or {}
        ready_map = factset.decision_ready()
        ready = set(ready_map.keys())
        bound_keys = tuple(sorted(rules.keys()))
        used_ids: set = set()

        opinions: List[ReviewOpinion] = []
        blocked: List[BlockedOpinion] = []
        corrections: List[SeverityCorrection] = []
        unresolved: List[UnresolvedEntry] = []
        judged = 0
        not_applicable = 0
        unresolved_count = 0

        for evaluation in evaluations:
            key = "%s@%s" % (evaluation.rule_id, evaluation.rule_version)
            rule = rules.get(key)

            # --- 覆盖度分类：每条规则有且仅归入一类 ---
            if evaluation.coverage is CoverageFlag.NOT_APPLICABLE:
                not_applicable += 1
                continue
            if evaluation.coverage is CoverageFlag.UNRESOLVED:
                unresolved_count += 1
                unresolved.append(
                    UnresolvedEntry(
                        rule_id=evaluation.rule_id,
                        rule_version=evaluation.rule_version,
                        reason_code=evaluation.reason_code or UnresolvedReasonCode.ENGINE_ERROR,
                        detail=evaluation.reason,
                    )
                )
                continue

            judged += 1

            # --- 生成草稿 ---
            facts = ready_map
            draft = self.writer(rule, evaluation, facts) if rule is not None else OpinionDraft(
                rule_id=evaluation.rule_id,
                rule_version=evaluation.rule_version,
                severity=evaluation.severity,
                fact_refs=tuple(evaluation.fact_refs),
                rationale="",
            )

            # --- 引用三件套闸门（FR-AUDIT-07）---
            missing = self.gate.check(draft, evaluation, rule=rule)
            if missing:
                blocked.append(
                    BlockedOpinion(
                        rule_id=draft.rule_id,
                        rule_version=draft.rule_version,
                        reason="引用三件套不完整",
                        missing=missing,
                        detail="缺 %s，意见不进入输出通道" % ("、".join(missing),),
                    )
                )
                continue

            # --- 禁止行为检测（quotes 先解析再送检，引用通道不留绕过口）---
            effective_quotes = tuple(quotes.get(key, draft.quotes))
            hits = self.detector.detect(
                draft, evaluation,
                bound_rule_keys=bound_keys,
                decidable_fact_names=tuple(sorted(ready)),
                rule=rule,
                quotes=effective_quotes,
            )
            if hits:
                blocked.append(
                    BlockedOpinion(
                        rule_id=draft.rule_id,
                        rule_version=draft.rule_version,
                        reason="命中禁止行为",
                        forbidden=tuple(h.code for h in hits),
                        detail="；".join(h.detail for h in hits),
                    )
                )
                continue

            # --- 严重度一律取自规则库（FR-AUDIT-03 / FB-04）---
            severity = rule.severity if rule is not None else evaluation.severity
            if draft.severity != severity:
                corrections.append(
                    SeverityCorrection(
                        rule_id=draft.rule_id,
                        rule_version=draft.rule_version,
                        proposed=draft.severity,
                        applied=severity,
                    )
                )

            # --- 引用三元组：field_path / value / source_channel（FR-AUDIT-06）---
            # 取值优先用判定实际使用的值（evaluation.values），来源通道取事实集登记。
            eval_values = dict(evaluation.values)
            citations = []
            for ref in draft.fact_refs:
                field = factset.get(ref)
                channels = tuple(c.value for c in field.channels) if field is not None else ()
                value = eval_values[ref] if ref in eval_values else facts.get(ref)
                citations.append(FactCitation(field_path=ref, value=value, source_channel=channels))

            # --- 意见编号（评审意见 5）---
            # 有 case_id：OP-<case_id>-<seq>，案件内唯一，卷宗可直接按它建键。
            # 无 case_id：退回 OP-<rule_id>-<version>，但同一次 build 内重复时补序号，
            # 杜绝"同号静默覆盖"。
            if case_id:
                opinion_id = "OP-%s-%03d" % (case_id, start_seq + len(opinions) + 1)
            else:
                base_id = "OP-%s-%s" % (draft.rule_id, draft.rule_version)
                opinion_id = base_id
                dup = 2
                while opinion_id in used_ids:
                    opinion_id = "%s-%d" % (base_id, dup)
                    dup += 1
            used_ids.add(opinion_id)

            opinions.append(
                ReviewOpinion(
                    opinion_id=opinion_id,
                    rule_id=draft.rule_id,
                    rule_version=draft.rule_version,
                    severity=severity,
                    fact_refs=tuple(draft.fact_refs),
                    rationale=draft.rationale,
                    coverage=CoverageFlag.JUDGED,
                    quotes=effective_quotes,
                    unresolved_reason_code=None,
                    origin=draft.origin,
                    fact_citations=tuple(citations),
                )
            )

        coverage = CoverageSummary(
            judged=judged,
            not_applicable=not_applicable,
            unresolved=unresolved_count,
            total=len(evaluations),
            unresolved_entries=tuple(unresolved),
            bound_rule_total=bound_rule_total,
        )
        return OpinionBuildResult(
            opinions=tuple(opinions),
            blocked=tuple(blocked),
            corrections=tuple(corrections),
            coverage=coverage,
        )
