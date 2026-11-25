"""规则模型（PRD 第五章 核心机制一）。

一条规则 = 可判定的 if-then 表达式 + 身份 + 版本 + 适用范围 + 出处。
字段缺失即不允许入库（FR-RAG-03），因此校验放在模型构造时做，而不是等到发布流程。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple

from .enums import Severity

RULE_ID_RE = re.compile(r"^R-[A-Z]+-\d+$")
WILDCARD = "*"


@dataclass(frozen=True)
class Rule:
    """一条规则的一个版本。

    注意：`rule_id` 相同而 `version` 不同的两条记录，是**同一规则的两个版本**，
    不是两条规则。版本选择由 RuleStore.bind() 按三键完成（FR-RAG-04）。
    """

    rule_id: str
    version: str
    effective_from: date
    effective_to: Optional[date]
    product_scope: Tuple[str, ...]
    org_scope: Tuple[str, ...]
    severity: Severity
    dsl: str
    required_facts: Tuple[str, ...] = ()
    source_ref: str = ""
    description: str = ""

    # ---------------- 构造期校验（FR-RAG-03 必填字段） ----------------
    def __post_init__(self) -> None:
        if not RULE_ID_RE.match(self.rule_id or ""):
            raise ValueError("rule_id 非法：%r（应形如 R-CR-013）" % (self.rule_id,))
        if not self.version:
            raise ValueError("规则 %s 缺少 version" % self.rule_id)
        if not self.product_scope:
            raise ValueError("规则 %s 缺少 product_scope" % self.rule_id)
        if not self.org_scope:
            raise ValueError("规则 %s 缺少 org_scope" % self.rule_id)
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError(
                "规则 %s@%s 的 effective_to 必须晚于 effective_from" % (self.rule_id, self.version)
            )
        if not self.dsl or not self.dsl.strip():
            raise ValueError("规则 %s@%s 缺少 dsl" % (self.rule_id, self.version))
        if not self.source_ref:
            raise ValueError("规则 %s@%s 缺少 source_ref（引用可回溯率的基础）" % (self.rule_id, self.version))
        if not isinstance(self.severity, Severity):
            raise ValueError("规则 %s@%s 的 severity 非法" % (self.rule_id, self.version))

    # ---------------- 适用范围判定 ----------------
    def covers_product(self, product_type: str) -> bool:
        return WILDCARD in self.product_scope or product_type in self.product_scope

    def covers_org(self, org_level: str) -> bool:
        return WILDCARD in self.org_scope or org_level in self.org_scope

    def effective_on(self, business_date: date) -> bool:
        """业务发生日是否落在生效区间内。

        左闭右开：[effective_from, effective_to)。effective_to 为 None 表示长期有效。
        """
        if business_date < self.effective_from:
            return False
        if self.effective_to is not None and business_date >= self.effective_to:
            return False
        return True

    @property
    def identity(self) -> str:
        return "%s@%s" % (self.rule_id, self.version)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "product_scope": list(self.product_scope),
            "org_scope": list(self.org_scope),
            "severity": self.severity.value,
            "dsl": self.dsl,
            "required_facts": list(self.required_facts),
            "source_ref": self.source_ref,
            "description": self.description,
        }
