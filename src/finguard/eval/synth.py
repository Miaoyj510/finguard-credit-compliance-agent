"""合成案件生成器：产出**材料**与**技术性真值**（切片 8.0）。

**为什么要有它**：金标标注需要**待标的材料**，而材料必须是合成的
（PRD 13.2 数据范围 100% 合成）。更关键的是——合成数据是**按规则生成的**，
所以事实取值、规则应命中状态、适用版本、覆盖度三类计数这些**技术性真值
在生成时就知道**，不必由任何人（或任何 Agent）去标。

于是金标分成两层（这是整个分工的基础）：

    · **技术性真值**（本模块产出）——事实取值、应命中 / 不适用 / 未判定、
      适用版本、严重度、覆盖度计数、未判定原因码
    · **判断性标注**（两个 Agent 各标一半）——识读是否正确、理由是否成立、
      严重度是否恰当、处置档位是否合理、是否算误报

生成器把两者一起产出：**真值**供系统比对，**材料**供裁判盲标。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..domain.rule import Rule
from ..rules.engine import RuleEngine
from ..rules.registry import RuleStore

__all__ = [
    "Material",
    "FactTruth",
    "RuleTruth",
    "SynthCase",
    "generate_cases",
    "industry_literals",
    "PRODUCTS",
    "ORG_LEVELS",
]

PRODUCTS = ("小额经营贷", "对公流贷")
ORG_LEVELS = ("一级分行", "二级分行")


@dataclass(frozen=True)
class Material:
    """一份材料（裁判**只看**它，不看真值、不看系统输出）。"""

    material_id: str
    kind: str
    title: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"material_id": self.material_id, "kind": self.kind,
                "title": self.title, "text": self.text}


@dataclass(frozen=True)
class FactTruth:
    """一个事实字段的真值。

    ``field_name`` 是**引擎取值的键**（与规则 DSL 里的 ``fact.xxx`` 对齐），
    ``field_path`` 是**展示路径**（如 ``income.monthly_revenue``）。两者分开，
    让规则表达式保持简洁、界面仍能显示可核对的完整路径。
    """

    field_path: str
    value: Any
    field_name: str = ""
    unit: str = ""
    source_channel: str = "ocr"

    def __post_init__(self) -> None:
        if not self.field_name:
            object.__setattr__(self, "field_name", self.field_path.rsplit(".", 1)[-1])

    def to_dict(self) -> Dict[str, Any]:
        return {"field_path": self.field_path, "field_name": self.field_name,
                "value": self.value, "unit": self.unit,
                "source_channel": self.source_channel}


@dataclass(frozen=True)
class RuleTruth:
    """一条规则的真值判定。"""

    rule_id: str
    rule_version: str
    coverage: str            # 已判定 / 不适用 / 未判定
    verdict: str             # 命中 / 不命中 / 不适用
    severity: str = ""
    reason_code: str = ""
    evidence_fields: Tuple[str, ...] = ()
    hit_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"rule_id": self.rule_id, "rule_version": self.rule_version,
                "coverage": self.coverage, "verdict": self.verdict,
                "severity": self.severity, "reason_code": self.reason_code,
                "evidence_fields": list(self.evidence_fields),
                "hit_label": self.hit_label}


@dataclass(frozen=True)
class SynthCase:
    """一件合成业务件：材料 + 真值。"""

    case_id: str
    product_type: str
    org_level: str
    business_date: str
    borrower: str
    materials: Tuple[Material, ...]
    facts: Tuple[FactTruth, ...]
    rules: Tuple[RuleTruth, ...]
    coverage: Mapping[str, int]
    unresolved_reasons: Tuple[str, ...]
    seed: int = 0

    def fact_map(self) -> Dict[str, Any]:
        return {f.field_name: f.value for f in self.facts}

    def ground_truth(self) -> Dict[str, Any]:
        """技术性真值（供系统输出比对，不含判断性标注）。"""
        return {
            "case_id": self.case_id,
            "seed": self.seed,
            "triple_key": {
                "business_date": self.business_date,
                "product_type": self.product_type,
                "org_level": self.org_level,
            },
            "facts": [f.to_dict() for f in self.facts],
            "rules": [r.to_dict() for r in self.rules],
            "coverage": dict(self.coverage),
            "unresolved_reasons": list(self.unresolved_reasons),
        }

    def task_pack(self) -> Dict[str, Any]:
        """给裁判的**任务包**：只有材料与待标字段清单，**不含真值**。"""
        return {
            "case_id": self.case_id,
            "materials": [m.to_dict() for m in self.materials],
            "fields_to_label": [
                "fact_recognition_ok",
                "rule_applicability",
                "reason_quality",
                "severity_appropriate",
                "is_false_positive",
            ],
            "scale": {
                "fact_recognition_ok": "对 | 错 | 缺漏",
                "rule_applicability": "同意 | 不同意（请注明）",
                "reason_quality": "成立 | 部分成立 | 不成立",
                "severity_appropriate": "恰当 | 偏高 | 偏低",
                "is_false_positive": "是 | 否 | 无法判断",
            },
        }


def _fmt(amount: float) -> str:
    return "{:,.0f}".format(amount)


def _material(case_id: str, index: int, kind: str, title: str, text: str) -> Material:
    return Material(material_id="%s-M%02d" % (case_id, index), kind=kind,
                    title=title, text=text)


def industry_literals(rules: Sequence[Rule]) -> Tuple[str, ...]:
    """从规则 DSL 里抓出 ``fact.industry == '...'`` 的字面量。

    **不硬编码行业名**——行业取值随规则库走，规则改了生成器自动跟上，
    不会出现"生成器用的行业名和规则里的对不上"这种静默失配。
    """
    found = set()
    for rule in rules:
        for match in re.finditer(r"fact\.industry\s*==\s*'([^']+)'", rule.dsl):
            found.add(match.group(1))
    return tuple(sorted(found))


def generate_cases(
    rules: Sequence[Rule],
    *,
    count: int = 30,
    seed: int = 20261128,
    case_prefix: str = "FG-G",
    binding_date: Optional[date] = None,
) -> Tuple[SynthCase, ...]:
    """生成 ``count`` 件合成业务件（材料 + 真值）。

    真值路径与系统路径**独立但同源**：都用 ``RuleStore.bind``（三键取版本）
    + ``RuleEngine.evaluate``，但生成器额外知道"事实真值"，因此能给出
    覆盖度三类计数与未判定原因码。
    """
    engine = RuleEngine()
    store = RuleStore(rules)
    industries = industry_literals(rules) or ("制造业",)
    all_industries = tuple(sorted(set(industries) | {"制造业", "批发和零售业"}))
    out: List[SynthCase] = []

    for index in range(count):
        case_seed = seed + index
        crng = random.Random(case_seed)
        case_id = "%s-%04d" % (case_prefix, index + 1)
        product = crng.choice(PRODUCTS)
        org = crng.choice(ORG_LEVELS)
        if binding_date is not None:
            business_date = binding_date
        elif index % 3 == 0:
            # 三分之一的案件落在 R-CR-041 两个版本的重叠窗口内，
            # 以覆盖「须人工判定」这条分支（FR-RAG-05）。
            business_date = date(2025, 6, 1) + timedelta(days=crng.randint(0, 180))
        else:
            business_date = date(2026, 1, 1) + timedelta(days=crng.randint(0, 200))
        industry = crng.choice(all_industries)
        borrower = "演示客户 %03d 号（合成）" % (index + 1)

        # ---- 事实（真值） ----
        monthly_revenue = crng.choice([300000, 450000, 620000, 820000, 1000000])
        declared_revenue = crng.choice([300000, 450000, 620000, 820000, 1000000])
        flow_total = int(declared_revenue * 0.9)
        tenor = crng.choice([12, 24, 36, 48])
        loan_amount = crng.choice([200000, 350000, 500000])
        branch_count = crng.choice([1, 1, 2, 3])
        approval_limit = crng.choice([300000, 500000, 800000])
        approver_authority = approval_limit + crng.choice([-100000, 0, 100000])

        facts = (
            FactTruth("income.monthly_revenue", monthly_revenue,
                      unit="元", source_channel="ocr"),
            FactTruth("income.declared_revenue", declared_revenue,
                      unit="元", source_channel="ocr"),
            FactTruth("income.flow_6m_total", flow_total,
                      unit="元", source_channel="api"),
            FactTruth("loan.tenor_months", tenor,
                      unit="月", source_channel="ocr"),
            FactTruth("loan.amount", loan_amount,
                      unit="元", source_channel="ocr"),
            FactTruth("borrower.industry", industry,
                      unit="", source_channel="ocr"),
            FactTruth("borrower.branch_count", branch_count,
                      unit="家", source_channel="ocr"),
            FactTruth("credit.approval_limit", approval_limit,
                      unit="元", source_channel="api"),
            FactTruth("credit.approver_authority", approver_authority,
                      unit="元", source_channel="api"),
        )
        fact_map = {f.field_name: f.value for f in facts}

        # ---- 规则真值：自己细分三类成因 ----
        #
        # 切片 1 的 ``RuleStore.bind()`` 把「产品/机构范围排除」与「时间版本缺口」
        # 都归为 NO_VERSION_MATCH（切片 1 评审意见 9）。但对**真值**来说两者语义
        # 完全不同——前者是"这条规则根本不适用于本案"，后者才是"该适用却没有
        # 适用版本"。所以真值侧自己细分，不复用 bind() 的粗分类。
        truth_rules: List[RuleTruth] = []
        judged = na = unresolved = 0
        unresolved_reasons: List[str] = []

        # 按 rule_id 分组：**同一 rule_id 的多个版本是同一条规则的候选版本**，
        # 不是多条规则——真值里一条规则只能占一行。
        grouped: Dict[str, List[Rule]] = {}
        for rule in rules:
            grouped.setdefault(rule.rule_id, []).append(rule)

        for rule_id, versions in sorted(grouped.items()):
            probe = versions[0]
            if not (probe.covers_product(product) and probe.covers_org(org)):
                truth_rules.append(RuleTruth(
                    rule_id, probe.version, "不适用", "不适用",
                    severity=probe.severity.value,
                    evidence_fields=tuple(probe.required_facts),
                ))
                na += 1
                continue

            effective_versions = [r for r in versions if r.effective_on(business_date)]
            if len(effective_versions) == 0:
                truth_rules.append(RuleTruth(
                    rule_id, probe.version, "未判定", "不适用",
                    severity=probe.severity.value, reason_code="NO_VERSION_MATCH",
                    evidence_fields=tuple(probe.required_facts),
                ))
                unresolved += 1
                unresolved_reasons.append("NO_VERSION_MATCH")
                continue
            if len(effective_versions) > 1:
                truth_rules.append(RuleTruth(
                    rule_id, " / ".join(v.version for v in effective_versions),
                    "未判定", "不适用",
                    severity=probe.severity.value, reason_code="AMBIGUOUS_VERSION",
                    evidence_fields=tuple(probe.required_facts),
                ))
                unresolved += 1
                unresolved_reasons.append("AMBIGUOUS_VERSION")
                continue

            active = effective_versions[0]
            evaluation = engine.evaluate(active, fact_map)
            if evaluation.coverage.value == "未判定":
                code = (evaluation.reason_code.value
                        if evaluation.reason_code else "MISSING_FACT")
                truth_rules.append(RuleTruth(
                    active.rule_id, active.version, "未判定", "不适用",
                    severity=active.severity.value, reason_code=code,
                    evidence_fields=tuple(evaluation.fact_refs),
                ))
                unresolved += 1
                unresolved_reasons.append(code)
                continue
            truth_rules.append(RuleTruth(
                active.rule_id, active.version, "已判定", evaluation.verdict.value,
                severity=active.severity.value,
                evidence_fields=tuple(evaluation.fact_refs),
                hit_label=evaluation.hit_label or "",
            ))
            judged += 1

        # ---- 材料（裁判只看这个） ----
        materials = (
            _material(case_id, 1, "申请要素表", "授信申请要素",
                       "借款人：%s\n产品类型：%s\n行业：%s\n机构层级：%s\n"
                       "申请金额：%s 元\n申请期限：%d 个月\n分支机构数量：%d 家\n"
                       "经办日期：%s"
                       % (borrower, product, industry, org, _fmt(loan_amount),
                          tenor, branch_count, business_date.isoformat())),
            _material(case_id, 2, "财务要素", "营业收入申报（按月）",
                       "申报月营业收入：%s 元\n申报口径：月营业收入\n"
                       "报送方式：纸质扫描件（OCR 录入）" % _fmt(monthly_revenue)),
            _material(case_id, 3, "财务要素", "营业收入累计申报",
                       "累计申报营业收入：%s 元\n申报口径：近十二个月合计\n"
                       "报送方式：纸质扫描件（OCR 录入）" % _fmt(declared_revenue)),
            _material(case_id, 4, "账户流水", "近六个月账户流水汇总",
                       "账户流水合计：%s 元\n统计区间：近六个月\n"
                       "取数渠道：核心系统只读接口" % _fmt(flow_total)),
            _material(case_id, 5, "授权信息", "审批权限主数据",
                       "本笔审批人授权额度：%s 元\n审批人岗位：支行审批岗\n"
                       "额度来源：授权额度与审批权限主数据" % _fmt(approver_authority)),
        )

        out.append(SynthCase(
            case_id=case_id,
            product_type=product,
            org_level=org,
            business_date=business_date.isoformat(),
            borrower=borrower,
            materials=materials,
            facts=facts,
            rules=tuple(truth_rules),
            coverage={"judged": judged, "not_applicable": na,
                      "unresolved": unresolved, "total": len(truth_rules)},
            unresolved_reasons=tuple(unresolved_reasons),
            seed=case_seed,
        ))
    return tuple(out)