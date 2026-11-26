"""三处强制人审闸门（PRD 6.5 FR-GATE-01 ~ 06、8.7 闸门表）。

闸门是本产品**唯一**的结论出口，也是「人在环」的全部技术落点。四条硬规则：

G-1 三处闸门在编排拓扑中均为必经节点，状态机不提供绕过边。
G-2 签核动作绑定实名账号 + 时间戳 + 设备指纹 + IP；**不接受批量导入式签核**。
G-3 签核动作本身写入案件卷宗（含原始意见与修改后意见双版本）。
G-4 **超时只升级、不放行**；任何降级态下都不得自动产生"已签核"状态。
G-5 签核人不得是本阶段意见生成 Agent 的运行主体，且不得为规则发布人。

本模块只管闸门自身状态；"能不能走到闸门"由 states.py 与 orchestrator.py 保证。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .states import GateSignError, Stage

__all__ = [
    "GateState",
    "Signer",
    "Escalation",
    "Amendment",
    "GateRecord",
    "GATE_SPECS",
    "GATE_ID_BY_STAGE",
    "gate_id_for",
    "build_gates",
]


class GateState(str, Enum):
    """闸门状态。**没有 AUTO_APPROVED / TIMEOUT_PASS 之类"自动放行"取值**——
    这是刻意的：状态枚举本身就是一条产品约束（FR-GATE-04）。"""

    NOT_REACHED = "NOT_REACHED"   # 尚未到达
    PENDING = "PENDING"           # 已提交，等待签核
    APPROVED = "APPROVED"         # 已签核放行
    RETURNED = "RETURNED"         # 已退回修改


@dataclass(frozen=True)
class Signer:
    """签核人（自然人 + 其授权上下文）。

    FR-GATE-02 要求「岗位 + 授权层级 + 案件类型」三重校验；
    FR-GATE-03 要求「实名 + 设备指纹 + 不可代签」；
    G-5 要求签核人不得是意见生成 Agent 的运行主体，也不是规则发布人。
    """

    account_id: str              # 实名账号
    display_name: str
    role: str                    # 岗位
    authority_level: int         # 授权层级（数字越大权限越高）
    authorized_case_types: Tuple[str, ...]   # 有权签核的案件类型
    device_fingerprint: str      # 设备指纹
    ip: str                      # 来源 IP
    is_rule_author: bool = False        # 是否为本版本规则的发布人
    is_agent_principal: bool = False    # 是否为本阶段意见生成 Agent 的运行主体
    batch_mode: bool = False            # 是否批量导入式签核


@dataclass(frozen=True)
class Escalation:
    """一次超时升级记录。**升级不等于放行**（G-4）。"""

    level: int
    to_role: str
    escalated_at: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "to_role": self.to_role,
            "escalated_at": self.escalated_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Amendment:
    """人工改写留痕（FR-GATE-06）：原始意见与修改后意见**双版本并存**。"""

    rule_id: str
    rule_version: str
    original_rationale: str
    amended_rationale: str
    reason: str
    amended_by: str
    amended_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "original_rationale": self.original_rationale,
            "amended_rationale": self.amended_rationale,
            "reason": self.reason,
            "amended_by": self.amended_by,
            "amended_at": self.amended_at,
        }


@dataclass
class GateRecord:
    """一个闸门的完整记录（PRD 8.4 的 gates 段）。"""

    gate_id: str                       # GATE-A / GATE-B / GATE-C
    stage: Stage                       # 对应的闸门状态
    required_role: str                 # 签核岗位
    required_authority_level: int      # 授权层级下限
    required_case_type: str            # 案件类型（FR-GATE-02 第三重校验）
    timeout_hours: int                 # 超时时长（自建评测集口径 [D]）
    escalation_chain: Tuple[str, ...]  # 升级链（自上而下）
    _state: GateState = GateState.NOT_REACHED
    signed_by: Optional[str] = None
    signed_role: Optional[str] = None
    signed_at: Optional[str] = None
    device_fingerprint: Optional[str] = None
    ip: Optional[str] = None
    returned_reason: Optional[str] = None
    returned_by: Optional[str] = None
    deadline: Optional[str] = None
    escalations: Tuple[Escalation, ...] = ()
    amendments: Tuple[Amendment, ...] = ()
    submissions: int = 0

    # ---------------- 状态（只读） ----------------
    @property
    def state(self) -> GateState:
        return self._state

    def approval_evidence(self) -> Optional[Dict[str, Any]]:
        """签核证据。

        放行时**必须**同时满足：状态为 APPROVED，且实名账号、时间戳、
        设备指纹、IP 四项齐全。少任何一项都视为**未签核**——
        这样即使有人绕过方法直接改写状态字段，也拿不到放行资格。
        """
        if self._state is not GateState.APPROVED:
            return None
        if not (self.signed_by and self.signed_at and self.device_fingerprint and self.ip):
            return None
        return {
            "signed_by": self.signed_by,
            "signed_role": self.signed_role,
            "signed_at": self.signed_at,
            "device_fingerprint": self.device_fingerprint,
            "ip": self.ip,
        }

    # ---------------- 生命周期 ----------------
    def open(self, now: str, deadline: Optional[str] = None) -> None:
        """提交到闸门（进入 PENDING）。重复提交计数，但不改变状态语义。"""
        if self._state is GateState.APPROVED:
            raise GateSignError("闸门 %s 已签核，不可再次提交" % self.gate_id)
        self._state = GateState.PENDING
        self.submissions += 1
        self.deadline = deadline or self._default_deadline(now)

    def _default_deadline(self, now: str) -> str:
        base = datetime.strptime(now, "%Y-%m-%dT%H:%M:%S")
        return (base + timedelta(hours=self.timeout_hours)).isoformat()

    def sign(self, signer: Signer, now: str) -> None:
        """签核放行。

        任何一项校验不过都抛 GateSignError，**闸门状态保持不变**（不产生半成品状态）。
        """
        if self._state is GateState.APPROVED:
            raise GateSignError("闸门 %s 已签核，不可重复签核" % self.gate_id)
        if self._state is not GateState.PENDING:
            raise GateSignError(
                "闸门 %s 当前状态 %s 不可签核（须先提交到闸门）" % (self.gate_id, self._state.value)
            )
        # G-2：实名 + 设备指纹 + IP，缺一不可；批量导入式签核直接拒收
        if signer.batch_mode:
            raise GateSignError("不接受批量导入式签核（FR-GATE-03）")
        for name, value in (
            ("account_id", signer.account_id),
            ("device_fingerprint", signer.device_fingerprint),
            ("ip", signer.ip),
        ):
            if not isinstance(value, str) or not value.strip():
                raise GateSignError("签核人缺少 %s（FR-GATE-03 不可代签）" % name)
        # FR-GATE-02：岗位 + 授权层级 + 案件类型
        if signer.role != self.required_role and signer.role not in self.escalation_chain:
            raise GateSignError(
                "签核人岗位 %r 无权签核 %s（需 %s 或升级链上的上级岗位）"
                % (signer.role, self.gate_id, self.required_role)
            )
        if signer.authority_level < self.required_authority_level:
            raise GateSignError(
                "签核人授权层级 %d 低于 %s 要求的 %d"
                % (signer.authority_level, self.gate_id, self.required_authority_level)
            )
        if self.required_case_type not in signer.authorized_case_types:
            raise GateSignError(
                "签核人无权签核案件类型 %s（FR-GATE-02 三重校验）" % self.required_case_type
            )
        # G-5：签核人不得是意见生成 Agent 的运行主体，也不得是规则发布人
        if signer.is_agent_principal:
            raise GateSignError("签核人不得为本阶段意见生成 Agent 的运行主体（G-5）")
        if signer.is_rule_author:
            raise GateSignError("签核人不得为规则发布人（G-5 / FR-GOV-01 双人复核）")

        self._state = GateState.APPROVED
        self.signed_by = signer.account_id
        self.signed_role = signer.role
        self.signed_at = now
        self.device_fingerprint = signer.device_fingerprint
        self.ip = signer.ip

    def return_to_author(self, signer: Signer, reason: str, now: str) -> None:
        """退回修改。理由必填（没有理由的退回无法复盘）。"""
        if self._state is not GateState.PENDING:
            raise GateSignError(
                "闸门 %s 当前状态 %s 不可退回" % (self.gate_id, self._state.value)
            )
        if not isinstance(reason, str) or not reason.strip():
            raise GateSignError("退回必须填写理由")
        if not isinstance(signer.account_id, str) or not signer.account_id.strip():
            raise GateSignError("退回人缺少实名账号")
        self._state = GateState.RETURNED
        self.returned_reason = reason
        self.returned_by = signer.account_id

    def check_timeout(self, now: str) -> Optional[Escalation]:
        """超时检查：**只升级，不放行**（FR-GATE-04 / G-4）。

        返回本次产生的升级记录；未超时或已到升级链顶端返回 None。
        无论是否超时，闸门状态都**保持 PENDING**。
        """
        if self._state is not GateState.PENDING:
            return None
        if not self.deadline:
            return None
        if now <= self.deadline:
            return None
        level = len(self.escalations)
        if level >= len(self.escalation_chain):
            return None                      # 已到最高级，仍保持 PENDING，不放行
        escalation = Escalation(
            level=level + 1,
            to_role=self.escalation_chain[level],
            escalated_at=now,
            reason="闸门 %s 超时 %d 小时未签核，升级至 %s"
                   % (self.gate_id, self.timeout_hours, self.escalation_chain[level]),
        )
        self.escalations = self.escalations + (escalation,)
        return escalation

    def record_amendment(
        self,
        rule_id: str,
        rule_version: str,
        original_rationale: str,
        amended_rationale: str,
        reason: str,
        amended_by: str,
        amended_at: str,
    ) -> Amendment:
        """登记一条人工改写（FR-GATE-06）。

        **原始意见只增不改**：本方法只追加，不提供任何覆盖 / 删除原始意见的入口。
        """
        if not isinstance(reason, str) or not reason.strip():
            raise GateSignError("人工改写必须填写修改理由（FR-GATE-06）")
        amendment = Amendment(
            rule_id=rule_id,
            rule_version=rule_version,
            original_rationale=original_rationale,
            amended_rationale=amended_rationale,
            reason=reason,
            amended_by=amended_by,
            amended_at=amended_at,
        )
        self.amendments = self.amendments + (amendment,)
        return amendment

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "stage": self.stage.value,
            "state": self._state.value,
            "required_role": self.required_role,
            "required_authority_level": self.required_authority_level,
            "required_case_type": self.required_case_type,
            "timeout_hours": self.timeout_hours,
            "signed_by": self.signed_by,
            "signed_role": self.signed_role,
            "signed_at": self.signed_at,
            "device_fingerprint": self.device_fingerprint,
            "ip": self.ip,
            "returned_reason": self.returned_reason,
            "returned_by": self.returned_by,
            "deadline": self.deadline,
            "submissions": self.submissions,
            "escalations": [e.to_dict() for e in self.escalations],
            "amendments": [a.to_dict() for a in self.amendments],
        }


#: 三处闸门的规格（PRD 8.7 闸门表；超时时长为自建评测集口径 [D]）
GATE_SPECS: Dict[str, Dict[str, Any]] = {
    "GATE-A": {
        "stage": Stage.GATE_A,
        "required_role": "内控合规审查岗",
        "required_authority_level": 2,
        "required_case_type": "corp_credit_review",
        "timeout_hours": 24,
        "escalation_chain": ("上级合规负责人", "风控负责人"),
    },
    "GATE-B": {
        "stage": Stage.GATE_B,
        "required_role": "风险研判岗",
        "required_authority_level": 3,
        "required_case_type": "corp_credit_review",
        "timeout_hours": 24,
        "escalation_chain": ("风险管理部负责人",),
    },
    "GATE-C": {
        "stage": Stage.GATE_C,
        "required_role": "贷后管理岗",
        "required_authority_level": 2,
        "required_case_type": "post_monitoring",
        "timeout_hours": 48,
        "escalation_chain": ("贷后管理部负责人", "分管行长"),
    },
}


#: 闸门状态 -> 闸门编号（Stage 用 GATE_A，GATE_SPECS 用 GATE-A，两套命名不要混）
GATE_ID_BY_STAGE: Dict[Stage, str] = {
    Stage.GATE_A: "GATE-A",
    Stage.GATE_B: "GATE-B",
    Stage.GATE_C: "GATE-C",
}


def gate_id_for(stage: Stage) -> str:
    """由闸门状态取闸门编号。"""
    return GATE_ID_BY_STAGE[stage]


def build_gates() -> Dict[str, GateRecord]:
    """构造三处闸门记录。"""
    gates: Dict[str, GateRecord] = {}
    for gate_id, spec in GATE_SPECS.items():
        gates[gate_id] = GateRecord(
            gate_id=gate_id,
            stage=spec["stage"],
            required_role=spec["required_role"],
            required_authority_level=spec["required_authority_level"],
            required_case_type=spec["required_case_type"],
            timeout_hours=spec["timeout_hours"],
            escalation_chain=spec["escalation_chain"],
        )
    return gates