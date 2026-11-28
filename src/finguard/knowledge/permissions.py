"""权限模型（PRD 9.4 权限模型 · 9.4 权限三原则 · PRD 11.4 S-06 越权检索）。

七层角色与四类资源的读权限。这里只管**读**，因为本产品的写入
只经由受控接口与人工操作（PRD 9.4 权限三原则第 2 条「只读优先」）。

三条原则的落点：

1. **职责分离**：规则发布人 ≠ 规则作者 ≠ 该案件签核人（FR-GOV-01、G-5）
   —— 体现在 ``sign_gate_role`` 与发布权限的分离上。
2. **只读优先**：Agent 与系统集成账号一律只读业务数据。
3. **最小披露**：个案数据默认脱敏；跨案件统计在聚合视图层进行。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple

from .corpus import Sensitivity

__all__ = [
    "Role",
    "ROLE_LABELS",
    "ROLE_LEVELS",
    "SIGN_GATE_ROLE",
    "CAN_READ_DOCUMENTS",
    "Permission",
    "can_read_documents",
    "sign_gate_for_role",
]


class Role(str, Enum):
    """PRD 9.4 的七层角色。"""

    L1 = "制度与内控管理岗"
    L2 = "内控合规审查岗"
    L3 = "风险研判岗"
    L4 = "贷后管理岗"
    L5 = "授信审查岗"
    L6 = "内审稽核岗"
    L7 = "运维/系统管理员"


ROLE_LABELS: Mapping[str, str] = {
    Role.L1.value: "制度与内控管理岗",
    Role.L2.value: "内控合规审查岗",
    Role.L3.value: "风险研判岗",
    Role.L4.value: "贷后管理岗",
    Role.L5.value: "授信审查岗",
    Role.L6.value: "内审稽核岗",
    Role.L7.value: "运维/系统管理员",
}

#: 角色层级（数字越小权限越高）
ROLE_LEVELS: Mapping[str, int] = {
    Role.L1.value: 1,
    Role.L2.value: 2,
    Role.L3.value: 3,
    Role.L4.value: 4,
    Role.L5.value: 5,
    Role.L6.value: 6,
    Role.L7.value: 7,
}

#: 可以签核的闸门（FR-GATE-02 的岗位维度）
SIGN_GATE_ROLE: Mapping[str, str] = {
    Role.L2.value: "GATE-A",
    Role.L3.value: "GATE-B",
    Role.L4.value: "GATE-C",
}

#: 可读文档库内容的角色。**L7 运维无内容读**（PRD 9.4 末行）
CAN_READ_DOCUMENTS: Tuple[str, ...] = (
    Role.L1.value, Role.L2.value, Role.L3.value,
    Role.L4.value, Role.L5.value, Role.L6.value,
)


@dataclass(frozen=True)
class Permission:
    """一个角色在某类资源上的权限快照。"""

    role: str
    level: int
    can_read_documents: bool
    can_read_restricted: bool
    sign_gate: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "role": self.role,
            "level": self.level,
            "can_read_documents": self.can_read_documents,
            "can_read_restricted": self.can_read_restricted,
            "sign_gate": self.sign_gate,
        }


def can_read_documents(role: str, sensitivity: Sensitivity = Sensitivity.INTERNAL) -> bool:
    """该角色能否读该敏感级别的文档内容。

    · 角色不在文档读取名单里 → 否（PRD 9.4：运维无内容读）
    · ``restricted`` 级文档额外要求 L1 / L6（制度管理与内审）
    """
    if role not in CAN_READ_DOCUMENTS:
        return False
    if sensitivity is Sensitivity.RESTRICTED:
        return role in (Role.L1.value, Role.L6.value)
    return True


def sign_gate_for_role(role: str) -> Optional[str]:
    """该角色可签核的闸门（无签核权返回 None）。"""
    return SIGN_GATE_ROLE.get(role)


def permission_of(role: str) -> Permission:
    """角色的权限快照。"""
    return Permission(
        role=role,
        level=ROLE_LEVELS.get(role, 99),
        can_read_documents=role in CAN_READ_DOCUMENTS,
        can_read_restricted=role in (Role.L1.value, Role.L6.value),
        sign_gate=sign_gate_for_role(role),
    )