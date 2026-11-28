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
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .states import GateSignError, Stage


def _parse_time(text: Any, what: str = "时间") -> datetime:
    """把 ISO 8601 时间字符串解析成**无时区的 UTC** datetime。

    时间戳是签核证据与超时判定的共同口径，坏值必须响亮拒绝：
    · 非字符串 / 不可解析 → GateSignError（不是 ValueError，编排层统一口径）
    · 带时区 → 归一为 UTC 裸时间（否则 naive 与 aware 比较会 TypeError）
    · 微秒精度 → 接受（时钟常见格式）
    """
    if not isinstance(text, str):
        raise GateSignError("%s须为 ISO 8601 字符串，收到 %r" % (what, text))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise GateSignError("%s无法解析（须为 ISO 8601）：%r" % (what, text))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

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
    required_case_type: str            # 该闸门默认服务的案件类型（规格注记；
                                       # FR-GATE-02 第三重校验实际比对的是本案 case_type）
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
    returned_device_fingerprint: Optional[str] = None
    returned_ip: Optional[str] = None
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
        """提交到闸门（进入 PENDING）。重复提交计数，但不改变状态语义。

        deadline 若由调用方给出，**解析后归一化入库**（统一 ISO 口径）——
        原样存异构字符串会让超时比较静默失效（评审 R10）。
        """
        if self._state is GateState.APPROVED:
            raise GateSignError("闸门 %s 已签核，不可再次提交" % self.gate_id)
        base = _parse_time(now, "闸门提交时间")
        if deadline is not None:
            normalized = _parse_time(deadline, "闸门超时期限").isoformat()
        else:
            normalized = (base + timedelta(hours=self.timeout_hours)).isoformat()
        self._state = GateState.PENDING
        self.submissions += 1
        self.deadline = normalized

    def _check_identity(self, signer: Signer, action: str) -> None:
        """G-2 身份完整性：实名 + 设备指纹 + IP，缺一不可；批量导入直接拒收。"""
        if signer.batch_mode:
            raise GateSignError("不接受批量导入式%s（FR-GATE-03）" % action)
        for name, value in (
            ("account_id", signer.account_id),
            ("device_fingerprint", signer.device_fingerprint),
            ("ip", signer.ip),
        ):
            if not isinstance(value, str) or not value.strip():
                raise GateSignError("签核人缺少 %s（FR-GATE-03 不可代签）" % name)

    def _check_qualification(self, signer: Signer, case_type: Optional[str]) -> None:
        """FR-GATE-02 三重校验 + G-5 职责分离。

        「案件类型」校验的对象是**本案的 case_type**（调用方传入），
        `required_case_type` 只是该闸门默认服务的案件类型（规格注记）——
        拿闸门规格常量去做授权判断会让「本案是什么类型」完全不参与校验，
        任何拿到闸门岗位的人都能替**任意类型案件**签核（评审 R11）。
        """
        if signer.role != self.required_role and signer.role not in self.escalation_chain:
            raise GateSignError(
                "签核人岗位 %r 无权操作 %s（需 %s 或升级链上的上级岗位）"
                % (signer.role, self.gate_id, self.required_role)
            )
        # C1 决策（PRD 8.7 字面口径）：升级链写在**超时路径**里，因此上级岗位
        # 必须先等到超时升级发生，才可接手签核。未超时就被上级接签，
        # 等同于 L1 静默放弃签核权——那是"绕过"，不是"升级"。
        if signer.role != self.required_role and not self.escalations:
            raise GateSignError(
                "闸门 %s 尚未超时升级，上级岗位 %r 不可提前接签"
                "（PRD 8.7 升级链属超时路径）" % (self.gate_id, signer.role)
            )
        if signer.authority_level < self.required_authority_level:
            raise GateSignError(
                "签核人授权层级 %d 低于 %s 要求的 %d"
                % (signer.authority_level, self.gate_id, self.required_authority_level)
            )
        effective_case_type = case_type if case_type is not None else self.required_case_type
        if effective_case_type not in signer.authorized_case_types:
            raise GateSignError(
                "签核人无权签核本案类型 %s（FR-GATE-02 三重校验）" % effective_case_type
            )
        # G-5：签核人不得是意见生成 Agent 的运行主体，也不得是规则发布人
        if signer.is_agent_principal:
            raise GateSignError("签核人不得为本阶段意见生成 Agent 的运行主体（G-5）")
        if signer.is_rule_author:
            raise GateSignError("签核人不得为规则发布人（G-5 / FR-GOV-01 双人复核）")

    def sign(self, signer: Signer, now: str, *, case_type: Optional[str] = None) -> None:
        """签核放行。

        任何一项校验不过都抛 GateSignError，**闸门状态保持不变**（不产生半成品状态）。
        `case_type` 为**本案实际类型**；不传时退回闸门规格（直接操作记录的旧调用兼容）。
        """
        if self._state is GateState.APPROVED:
            raise GateSignError("闸门 %s 已签核，不可重复签核" % self.gate_id)
        if self._state is not GateState.PENDING:
            raise GateSignError(
                "闸门 %s 当前状态 %s 不可签核（须先提交到闸门）" % (self.gate_id, self._state.value)
            )
        moment = _parse_time(now, "签核时间")
        self._check_identity(signer, "签核")
        self._check_qualification(signer, case_type)

        self._state = GateState.APPROVED
        self.signed_by = signer.account_id
        self.signed_role = signer.role
        self.signed_at = moment.isoformat()
        self.device_fingerprint = signer.device_fingerprint
        self.ip = signer.ip

    def return_to_author(
        self, signer: Signer, reason: str, now: str, *, case_type: Optional[str] = None
    ) -> None:
        """退回修改。

        退回与签核同为**闸门签核动作**（结论取反也是结论）：理由必填，
        且必须通过与签核完全相同的身份 + 资格校验（FR-GATE-02/03、G-5）——
        否则任何账号都能批量退回拖死案件流，退回痕迹也无法归责到人（评审 R6）。
        """
        if self._state is not GateState.PENDING:
            raise GateSignError(
                "闸门 %s 当前状态 %s 不可退回" % (self.gate_id, self._state.value)
            )
        if not isinstance(reason, str) or not reason.strip():
            raise GateSignError("退回必须填写理由")
        moment = _parse_time(now, "退回时间")
        self._check_identity(signer, "退回")
        self._check_qualification(signer, case_type)
        self._state = GateState.RETURNED
        self.returned_reason = reason
        self.returned_by = signer.account_id
        self.returned_device_fingerprint = signer.device_fingerprint
        self.returned_ip = signer.ip

    def check_timeout(self, now: str) -> Optional[Escalation]:
        """超时检查：**只升级，不放行**（FR-GATE-04 / G-4）。

        返回本次产生的升级记录；未超时或已到升级链顶端返回 None。
        无论是否超时，闸门状态都**保持 PENDING**。
        超时判定按解析后的 datetime 比较（字符串比较对异构格式会静默失效）。
        """
        if self._state is not GateState.PENDING:
            return None
        if not self.deadline:
            return None
        moment = _parse_time(now, "超时检查时间")
        if moment <= _parse_time(self.deadline, "闸门超时期限"):
            return None
        level = len(self.escalations)
        if level >= len(self.escalation_chain):
            return None                      # 已到最高级，仍保持 PENDING，不放行
        escalation = Escalation(
            level=level + 1,
            to_role=self.escalation_chain[level],
            escalated_at=moment.isoformat(),
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
            "returned_device_fingerprint": self.returned_device_fingerprint,
            "returned_ip": self.returned_ip,
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