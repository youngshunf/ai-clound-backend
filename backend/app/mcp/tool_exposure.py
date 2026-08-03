"""统一工具暴露管线 `ToolExposurePolicy`（doc18 §3 · 实施/103）。

云端 MCP 面「某分身此刻能不能看见/调用某工具」此前散在三处各写判定：
发现面 `tool_directory._can_discover`（external 白名单 + 三态 deny）、
执行面 `server.call_tool`（tool_mode 三态）、转发面
`hasn.cloud.tool.call`（重入 call_tool）。本模块把判定收敛为单一纯函数
`evaluate`，各面消费同一投影（D6 四面同源铁律）。

⚠️ 暴露只受「权限 + 付费墙」限制，与工具在本地还是云端无关（福仔 2026-07-10 原则）：
能搜就能用，分身两面（hasn-local + hasn-cloud）都能调。故本管线**不含**任何按运行位置
隐藏云端工具的门（原 TOOLMIG2-P4「运行位置收口」已整体退役；其依赖的运行位置快照亦已
随 H8「云端 Runtime 下线」从 AgentContext 删除）。

分期（实施/103）：U1 零行为变化收编，只装 G2 来源门 + G5 主人三态门（现状
收编，不新建语义）；G1 平台特权门（U2 = diag P3a）、G3 应用权益门（U3）、
G4 企业角色门（U4）在此管线上按 doc18 §3.2 顺序逐门加装。

纯函数约束（103 §8 性能条）：`evaluate` 零 IO——所有 per-agent 输入
（external 白名单、三态策略）已在 AgentContext
per-request 预取，tool.search 逐工具求值不放大查库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.app.mcp.platform_scopes import (
    PRIVILEGED_SCOPES,
    privileged_scopes_satisfied,
)
from backend.app.mcp.tool_app_registry import resolve_tool_app_id
from backend.common.security.scope_policy import MODE_ASK, MODE_DENY

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext
    from backend.app.mcp.tools.base import BaseTool

# ── 决策动作（doc18 §3.1：一次判定，多面消费）────────────────────────────
ACTION_HIDDEN = 'hidden'  # 不可见 + 执行面按 reason 映射错误（不确认存在性）
ACTION_VISIBLE_DENY = 'visible_deny'  # 可见（带引导）+ 执行面拒绝（U3 商业化引导才产出）
ACTION_ASK = 'ask'  # 可见 + 执行面挂审批（doc15 既有 ask 闸链路）
ACTION_ALLOW = 'allow'  # 可见 + 放行

# ── 命中门（审计口径：evaluate 返回值带 gate/reason，拒绝路径 log 记录命中门）──
GATE_PRIVILEGE = 'g1_privilege'
GATE_SOURCE = 'g2_source'
GATE_ENTITLEMENT = 'g3_entitlement'
GATE_ROLE = 'g4_role'
GATE_OWNER = 'g5_owner'

# ── HIDDEN reason（执行面据此映射错误码，保持与收编前逐位一致）──
REASON_PRIVILEGED = 'privileged'  # G1：特权 scope 未持有（执行面 TOOL_NOT_FOUND 泛化文案，不确认存在性）
REASON_EXTERNAL_NOT_BOUND = 'external_not_bound'  # G2：external 工具不在本 Agent binding 白名单
REASON_ROLE_INSUFFICIENT = 'enterprise_role_insufficient'  # G4：主人企业角色未被授予该能力族（与网关审计口径同串）
REASON_OWNER_DENIED = 'owner_denied'  # G5：owner 三态 deny（维持现状 deny 即隐身）
# G3 应用权益门的 reason 不在此枚举——直接透传合并准入 dict 的 reason（need_purchase /
# need_seat_assignment / need_enterprise_space / need_upgrade…），口径与 doc04 M1 一致。

# G3 里属于**应用生命周期**（而非商业化引导）的 reason：主人自己解不了，故工具面隐身
# （doc21 D-3）。`disabled` = 已下架、`need_beta`/`beta_pending` = 灰度内测未获批 / 审核中。
# 与本地 hasn-mcp `crates/hasn-mcp/src/app_gate.rs::HIDDEN_REASONS` 必须保持同一张表。
LIFECYCLE_HIDDEN_REASONS: frozenset[str] = frozenset({'disabled', 'need_beta', 'beta_pending'})


@dataclass(frozen=True)
class ExposureDecision:
    """单次 evaluate 的判定投影。

    发现面消费 `is_visible`（HIDDEN 过滤掉，VISIBLE_DENY/ASK 仍列出）；
    执行面按 action+reason 映射错误码 / 挂审批 / 放行。VISIBLE_DENY 带 `app_id`
    供发现面渲染 access_hint（引导购买/指派席位/切换企业空间）。
    """

    action: str
    gate: str | None = None
    reason: str | None = None
    app_id: str | None = None

    @property
    def is_hidden(self) -> bool:
        return self.action == ACTION_HIDDEN

    @property
    def is_visible(self) -> bool:
        return self.action != ACTION_HIDDEN

    @property
    def is_visible_deny(self) -> bool:
        return self.action == ACTION_VISIBLE_DENY


DECISION_ALLOW = ExposureDecision(ACTION_ALLOW)
DECISION_ASK = ExposureDecision(ACTION_ASK, gate=GATE_OWNER)


class ToolExposurePolicy:
    """五门管线单一接缝（doc18 §3.2：短路求值，顺序即优先级）。

    顺序理由：G1/G2 硬边界（存在性都不暴露）最前；G3 商业化（可见拒）在角色
    之前；G4 在 G5 之前保证「分身权限 ≤ 主人」；G5 永远最后——三态是 owner 的
    态度层，**只能收紧、绝不放行前面任何一门**。
    """

    def evaluate(self, agent_context: AgentContext, tool: BaseTool) -> ExposureDecision:
        # G1 平台特权门（doc18 §4.1）：工具的 required_scopes 里凡命中特权名单
        # （前缀 diag:/ops:/platform:）的都要被本 Agent 的 granted_privileged_scopes
        # 覆盖，否则 HIDDEN（发现面不列出、执行面 TOOL_NOT_FOUND，不确认存在性）。
        # 授予源仅 Admin 表 + ENV bootstrap，与已废弃的凭证 scopes 无关；持有者继续过 G5。
        required = getattr(tool, 'required_scopes', None) or []
        needed = frozenset(s for s in required if s in PRIVILEGED_SCOPES)
        if needed:
            granted = getattr(agent_context, 'granted_privileged_scopes', None) or frozenset()
            if not privileged_scopes_satisfied(needed, granted):
                return ExposureDecision(ACTION_HIDDEN, gate=GATE_PRIVILEGE, reason=REASON_PRIVILEGED)

        # G2 来源接入门（硬边界）· P7 第三方 MCP 网关：external 工具实例全局共享，发现/调用
        # 资格按本 Agent binding（gate1 owner 启用 + gate2 allowed_tools，由 server 每次调用前
        # 注入）per-request 过滤——不在授权集合的一律不可见，杜绝跨 Agent 串号。
        #
        # 注：曾在此按运行位置隐藏 deck/task/workflow（TOOLMIG2-P4「运行位置收口」），
        # 2026-07-10 福仔拍板整体退役——工具暴露只受权限 + 付费墙限制，与工具在本地/云端无关；
        # 能搜就能用。故此处不再有按位置的隐藏门（该位置快照已随 H8 从 AgentContext 删除）。
        tool_name = getattr(tool, 'name', '')
        if getattr(tool, 'source', 'platform') == 'external':
            allowed: set[str] = getattr(agent_context, 'external_allowed_tools', set()) or set()
            if tool_name not in allowed:
                return ExposureDecision(ACTION_HIDDEN, gate=GATE_SOURCE, reason=REASON_EXTERNAL_NOT_BOUND)

        # G3 应用权益门（U3·doc18 §4.3）：工具所属 app 合并准入不通 → VISIBLE_DENY(need_*)。
        # 详见 _entitlement_denial（纯查预取 app_access_by_id，零 IO）。
        entitlement_denial = self._entitlement_denial(agent_context, tool)
        if entitlement_denial is not None:
            return entitlement_denial

        # G4 企业角色门（U4·doc18 §4.4）：企业空间角色未授予该能力族 → HIDDEN(role)。
        # 当前 inert（策略表未落地），详见 _role_gate_denial。
        role_denial = self._role_gate_denial(agent_context, tool)
        if role_denial is not None:
            return role_denial

        # G5 主人三态门（维度①唯一权威·最后态度层）：deny→隐身；ask→挂审批；allow→放行。
        # 维度② 对象可达性不在管线里，由工具运行时返回。
        mode = agent_context.tool_mode(tool)
        if mode == MODE_DENY:
            return ExposureDecision(ACTION_HIDDEN, gate=GATE_OWNER, reason=REASON_OWNER_DENIED)
        if mode == MODE_ASK:
            return DECISION_ASK
        return DECISION_ALLOW

    def _entitlement_denial(self, agent_context: AgentContext, tool: BaseTool) -> ExposureDecision | None:
        """G3 应用权益门判定（doc18 §4.3 / doc21 D-3）：工具所属 app 的合并准入（owner∪enterprise
        权益 + 席位，doc04 M1）不通 → 按 reason 分派 HIDDEN / VISIBLE_DENY。纯查 AgentContext 预取的
        app_access_by_id（零 IO）；底座工具 app_id=None / 免费应用 / 未预取 → 该 app_id 缺席即返回
        None 跳过（never over-block）。

        **分派依据是「主人解不解得了」**（doc21 D-3，与本地 hasn-mcp `app_gate::decide` 同表）：

        - `disabled`（应用已下架）→ HIDDEN：主人**买不到**，可见拒只会让分身反复尝试、
          让主人看到一个不存在的东西；
        - `need_beta` / `beta_pending`（灰度内测未获批）→ HIDDEN：分身没法"申请内测"，
          看到一个调不动的工具纯浪费轮次。**注意这只是给分身的工具面**——给主人的应用中心
          （`workbench_domain_service.list_apps`）仍然照常可见并引导申请内测，出参零变化；
        - `need_purchase` / `need_upgrade` / `need_seat_assignment` / `need_enterprise_space`
          → VISIBLE_DENY：主人解得了，分身要能看见并如实转达。

        未登记的 reason（云端将来新增口径）保守按 VISIBLE_DENY 透传原文——不静默放行，
        也不冒充"工具不存在"。
        """
        app_id = resolve_tool_app_id(tool)
        if not app_id:
            return None
        access = (getattr(agent_context, 'app_access_by_id', None) or {}).get(app_id)
        if access is not None and not access.get('allowed', True):
            reason = access.get('reason') or 'need_purchase'
            action = ACTION_HIDDEN if reason in LIFECYCLE_HIDDEN_REASONS else ACTION_VISIBLE_DENY
            return ExposureDecision(action, gate=GATE_ENTITLEMENT, reason=reason, app_id=app_id)
        return None

    def _role_gate_denial(self, agent_context: AgentContext, tool: BaseTool) -> ExposureDecision | None:
        """G4 企业角色门判定（doc18 §4.4）：企业空间下，工具若声明企业能力族键
        `enterprise_capability`，需主人在当前激活企业的角色被授予该能力族，否则 HIDDEN(role)——
        保证「分身能力 ≤ 主人角色能力」（doc12/02 两刀之一，与 G5 三态取交集）。个人空间跳过。

        ⚠️ 外部硬依赖 doc12/02 角色→能力族策略表**尚未落地**（实施清单待实施），故本门当前 inert：
        `enterprise_capability_grants` 恒 None（策略表未解析 → 返回 None 跳过，never over-block）、
        且现无工具声明 `enterprise_capability`。策略表落地后，U5/doc12-02 P3 只需（a）给相关工具声明
        enterprise_capability（b）在 inject 预取主人角色能力族 → 本门即生效，evaluate 结构不动。
        """
        cap = getattr(tool, 'enterprise_capability', None)
        if not cap or agent_context.active_enterprise_id is None:
            return None
        grants = getattr(agent_context, 'enterprise_capability_grants', None)
        if grants is not None and cap not in grants:
            return ExposureDecision(ACTION_HIDDEN, gate=GATE_ROLE, reason=REASON_ROLE_INSUFFICIENT)
        return None

    def is_catalog_hidden(self, agent_context: AgentContext, tool: BaseTool) -> bool:
        """owner 权限页 catalog 是否隐藏该工具（第四暴露面收编，doc18 §3.2）。

        **只**消费 G1/G2 硬边界（存在性都不该暴露的门）：普通分身的权限页不列
        `diag:*` 等特权工具、也不列未绑定的 external 工具。**G5 三态永不参与**——
        deny 项必须留在权限页供 owner 改回（否则复刻 102-B3「单向门」）。
        因 evaluate 短路（顺序即优先级），HIDDEN 且 gate∈{G1,G2} 即硬边界命中。
        """
        decision = self.evaluate(agent_context, tool)
        return decision.is_hidden and decision.gate in (GATE_PRIVILEGE, GATE_SOURCE)


tool_exposure_policy = ToolExposurePolicy()
