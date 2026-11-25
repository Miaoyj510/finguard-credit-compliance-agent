"""演示用种子规则集。

⚠ 这些规则是**为演示与测试构造的合成数据**，不是任何机构的真实制度条款。
真实规则必须由 U5 制度管理岗在规则建模工作台维护，双人复核发布（FR-GOV-01 / FR-GOV-02）。
规则刻意覆盖了以下场景，便于验证三键取版本的各条分支：
  - R-CR-013  同一规则的多个版本（历史时点取旧版）
  - R-CR-021  小额经营贷专属
  - R-CR-030  大额授信专属（产品不适用 → 须人工判定）
  - R-CR-041  两个版本生效区间重叠（→ 须人工判定）
  - R-CR-050  仅一级分行适用（机构层级不适用 → 须人工判定）
"""
from __future__ import annotations

from datetime import date
from typing import List

from ..domain.enums import Severity
from ..domain.rule import Rule

RULES: List[Rule] = [
    # --- R-CR-013：同一规则的两个版本 ---
    Rule(
        rule_id="R-CR-013",
        version="3.0.0",
        effective_from=date(2024, 1, 1),
        effective_to=date(2025, 7, 1),
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        severity=Severity.ATTENTION,
        dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('申报营收与流水不符')",
        required_facts=("monthly_revenue", "declared_revenue"),
        source_ref="《信贷业务审查操作规程》第四条（2024 版，演示用合成条款）",
        description="申报月营收与流水合计勾稽（历史版本）",
    ),
    Rule(
        rule_id="R-CR-013",
        version="3.2.0",
        effective_from=date(2025, 7, 1),
        effective_to=None,
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.monthly_revenue < fact.declared_revenue THEN hit('申报营收与流水不符')",
        required_facts=("monthly_revenue", "declared_revenue"),
        source_ref="《信贷业务审查操作规程》第四条（2025 版，演示用合成条款）",
        description="申报月营收与流水合计勾稽（现行版本）",
    ),
    # --- R-CR-021：期限上限，小额经营贷专属 ---
    Rule(
        rule_id="R-CR-021",
        version="1.4.1",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        product_scope=("小额经营贷",),
        org_scope=("*",),
        severity=Severity.INFO,
        dsl="IF fact.tenor_months > 36 THEN hit('期限超出产品上限')",
        required_facts=("tenor_months",),
        source_ref="《小额经营贷产品管理办法》第十二条（演示用合成条款）",
        description="期限上限校验",
    ),
    # --- R-CR-030：大额授信专属，其他产品不适用 ---
    Rule(
        rule_id="R-CR-030",
        version="2.0.0",
        effective_from=date(2025, 1, 1),
        effective_to=None,
        product_scope=("大额授信",),
        org_scope=("*",),
        severity=Severity.CRITICAL,
        dsl="IF fact.loan_amount > 5000000 THEN hit('超权限大额授信')",
        required_facts=("loan_amount",),
        source_ref="《大额授信管理办法》第八条（演示用合成条款）",
        description="超权限大额授信识别",
    ),
    # --- R-CR-041：两个版本生效区间重叠（制度过渡期） ---
    Rule(
        rule_id="R-CR-041",
        version="1.0.0",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),
        product_scope=("小额经营贷",),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.industry == '房地产' THEN hit('行业集中度需复核')",
        required_facts=("industry",),
        source_ref="《行业集中度管理办法》第五条（演示用合成条款）",
        description="行业集中度复核（旧版）",
    ),
    Rule(
        rule_id="R-CR-041",
        version="1.1.0",
        effective_from=date(2025, 6, 1),
        effective_to=None,
        product_scope=("小额经营贷",),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.industry == '房地产' THEN hit('行业集中度需复核')",
        required_facts=("industry",),
        source_ref="《行业集中度管理办法》第五条（修订版，演示用合成条款）",
        description="行业集中度复核（修订版，与旧版区间重叠）",
    ),
    # --- R-CR-050：仅一级分行适用 ---
    Rule(
        rule_id="R-CR-050",
        version="1.0.0",
        effective_from=date(2025, 3, 1),
        effective_to=None,
        product_scope=("小额经营贷",),
        org_scope=("一级分行",),
        severity=Severity.MAJOR,
        dsl="IF fact.branch_count > 1 THEN hit('跨分支机构授信需上报')",
        required_facts=("branch_count",),
        source_ref="《授信权限管理办法》第十一条（演示用合成条款）",
        description="跨分支机构授信上报",
    ),
]


def seed_rules() -> List[Rule]:
    """返回一份新的种子规则列表（避免调用方误改全局对象）。"""
    return list(RULES)
