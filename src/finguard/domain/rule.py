"""规则模型（PRD 第五章 核心机制一）。

一条规则 = 可判定的 if-then 表达式 + 身份 + 版本 + 适用范围 + 出处。
字段缺失即不允许入库（FR-RAG-03），因此校验放在模型构造时做，而不是等到发布流程。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

from .enums import Severity

# re.ASCII：\d 只认 ASCII 数字，全角数字（０１３）不是合法序号
RULE_ID_RE = re.compile(r"^R-[A-Z]+-\d+$", re.ASCII)
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
    # 该规则的依据是否必须附原文片段（PRD 8.1 citation_requirement.quote_required）。
    # 默认 False：制度条款类依据可标 True，引用闸门据此要求 quotes 非空。
    quote_required: bool = False

    # ---------------- 构造期校验（FR-RAG-03 必填字段） ----------------
    def __post_init__(self) -> None:
        # fullmatch 而非 match：$ 会放过尾随换行，让 "R-CR-013\n" 绕过重复版本闸
        if not isinstance(self.rule_id, str) or not RULE_ID_RE.fullmatch(self.rule_id):
            raise ValueError("rule_id 非法：%r（应形如 R-CR-013，不含空白）" % (self.rule_id,))
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("规则 %s 缺少 version" % self.rule_id)
        # type() is date：datetime 是 date 的子类，但与 date 比较会 TypeError，须挡在构造期
        if type(self.effective_from) is not date:
            raise ValueError(
                "规则 %s@%s 缺少 effective_from 或类型非法（须为 datetime.date）"
                % (self.rule_id, self.version)
            )
        if self.effective_to is not None and type(self.effective_to) is not date:
            raise ValueError(
                "规则 %s@%s 的 effective_to 类型非法（须为 datetime.date 或 None）"
                % (self.rule_id, self.version)
            )
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError(
                "规则 %s@%s 的 effective_to 必须晚于 effective_from" % (self.rule_id, self.version)
            )
        for name in ("product_scope", "org_scope"):
            scope = self._normalize_scope(name, getattr(self, name))
            if not scope:
                raise ValueError("规则 %s 缺少 %s" % (self.rule_id, name))
            object.__setattr__(self, name, scope)
        object.__setattr__(
            self, "required_facts", self._normalize_scope("required_facts", self.required_facts)
        )
        if not isinstance(self.dsl, str) or not self.dsl.strip():
            raise ValueError("规则 %s@%s 缺少 dsl" % (self.rule_id, self.version))
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("规则 %s@%s 缺少 source_ref（引用可回溯率的基础）" % (self.rule_id, self.version))
        if not isinstance(self.severity, Severity):
            raise ValueError("规则 %s@%s 的 severity 非法" % (self.rule_id, self.version))

    @staticmethod
    def _normalize_scope(field_name: str, value) -> Tuple[str, ...]:
        """范围字段必须是 tuple/list of 非空字符串，统一规范化为 tuple。

        显式拒绝 str：("小额经营贷") 漏写逗号就是 str，`in` 会退化成子串匹配，
        让「小额」之类的产品名静默误绑到本规则——这是合规事故，不是小毛病。
        """
        if isinstance(value, str) or not isinstance(value, (tuple, list)):
            raise ValueError(
                "%s 须为字符串 tuple 或 list，得到 %r（单元素 tuple 别漏逗号）"
                % (field_name, value)
            )
        for item in value:
            if not isinstance(item, str) or not item.strip() or item != item.strip():
                raise ValueError(
                    "%s 含非法元素 %r（须为非空字符串、无首尾空白）" % (field_name, item)
                )
        return tuple(value)

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
            "quote_required": self.quote_required,
        }
