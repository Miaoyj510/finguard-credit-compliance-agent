"""贷后扫描规则种子（演示用合成规则）。

**与贷前共用同一个规则库与版本体系**（FR-POST-02）——贷后规则不是另起一套，
只是在本演示里新增了几条贷后特有的规则，并**复用了贷前的 R-CR-013**
（同一 rule_id、同一 version），用来证明「同一 rule_id 在贷前贷后两处的
version 语义一致」。

⚠ 这些是**为演示与测试构造的合成规则**，不是任何机构的真实制度条款。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

from ..domain.enums import Severity
from ..domain.rule import Rule
from .seeds import RULES as CREDIT_RULES

__all__ = ["POST_RULES", "post_rules", "post_rule_map"]


#: 贷前规则里被复用的那一条（证明版本语义一致）
REUSED_CREDIT_RULE_ID = "R-CR-013"
REUSED_CREDIT_RULE_VERSION = "3.2.0"

POST_RULES: List[Rule] = [
    # --- 复用贷前规则：同 rule_id 同 version ---
    next(r for r in CREDIT_RULES
         if r.rule_id == REUSED_CREDIT_RULE_ID and r.version == REUSED_CREDIT_RULE_VERSION),
    # --- 贷后特有规则 ---
    Rule(
        rule_id="R-PM-001",
        version="1.0.0",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        severity=Severity.CRITICAL,
        dsl="IF fact.days_past_due > 0 THEN hit('还款逾期')",
        required_facts=("days_past_due",),
        source_ref="《贷后管理办法》第九条（演示用合成条款）",
        description="还款逾期触发贷后预警",
    ),
    Rule(
        rule_id="R-PM-002",
        version="1.1.0",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        severity=Severity.MAJOR,
        dsl="IF fact.rating_downgrade >= 2 THEN hit('外部评级下调')",
        required_facts=("rating_downgrade",),
        source_ref="《贷后管理办法》第十二条（演示用合成条款）",
        description="评级下调达到阈值触发预警",
    ),
    Rule(
        rule_id="R-PM-003",
        version="1.0.0",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        product_scope=("小额经营贷", "对公流贷"),
        org_scope=("*",),
        severity=Severity.ATTENTION,
        dsl="IF fact.utilization_ratio > 0.9 THEN hit('额度使用率超阈值')",
        required_facts=("utilization_ratio",),
        source_ref="《贷后管理办法》第十五条（演示用合成条款）",
        description="额度使用率超阈值触发预警",
    ),
]


def post_rules() -> Tuple[Rule, ...]:
    return tuple(POST_RULES)


def post_rule_map() -> Dict[str, Rule]:
    """{"rule_id@version": Rule}——与贷前同一套取用方式。"""
    return {"%s@%s" % (r.rule_id, r.version): r for r in POST_RULES}