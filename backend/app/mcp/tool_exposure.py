"""统一工具暴露管线 `ToolExposurePolicy`（doc18 §3 · 实施/103）。

云端 MCP 面「某分身此刻能不能看见/调用某工具」此前散在三处各写判定：
发现面 `tool_directory._can_discover`（external 白名单 + 三态 deny + runtime 隐藏）、
执行面 `server.call_tool`（runtime 兜底 + tool_mode 三态）、转发面
`hasn.cloud.tool.call`（重入 call_tool）。本模块把判定收敛为单一纯函数
`evaluate`，各面消费同一投影（D6 四面同源铁律）。

分期（实施/103）：U1 零行为变化收编，只装 G2 来源门 + G5 主人三态门（现状
收编，不新建语义）；G1 平台特权门（U2 = diag P3a）、G3 应用权益门（U3）、
G4 企业角色门（U4）在此管线上按 doc18 §3.2 顺序逐门加装。

纯函数约束（103 §8 性能条）：`evaluate` 零 IO——所有 per-agent 输入
（external 白名单、三态策略、runtime_location）已在 AgentContext
per-request 预取，tool.search 逐工具求值不放大查库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.app.mcp.runtime_visibility import is_namespace_hidden_for_runtime
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
REASON_EXTERNAL_NOT_BOUND = 'external_not_bound'  # G2：external 工具不在本 Agent binding 白名单
REASON_RUNTIME_HIDDEN = 'runtime_hidden'  # G2：本地分身在云端面隐藏的命名空间（TOOLMIG2-P4）
REASON_OWNER_DENIED = 'owner_denied'  # G5：owner 三态 deny（维持现状 deny 即隐身）


@dataclass(frozen=True)
class ExposureDecision:
    """单次 evaluate 的判定投影。

    发现面消费 `is_visible`（HIDDEN 过滤掉，VISIBLE_DENY/ASK 仍列出）；
    执行面按 action+reason 映射错误码 / 挂审批 / 放行。
    """

    action: str
    gate: str | None = None
    reason: str | None = None

    @property
    def is_hidden(self) -> bool:
        return self.action == ACTION_HIDDEN

    @property
    def is_visible(self) -> bool:
        return self.action != ACTION_HIDDEN


DECISION_ALLOW = ExposureDecision(ACTION_ALLOW)
DECISION_ASK = ExposureDecision(ACTION_ASK, gate=GATE_OWNER)


class ToolExposurePolicy:
    """五门管线单一接缝（doc18 §3.2：短路求值，顺序即优先级）。

    顺序理由：G1/G2 硬边界（存在性都不暴露）最前；G3 商业化（可见拒）在角色
    之前；G4 在 G5 之前保证「分身权限 ≤ 主人」；G5 永远最后——三态是 owner 的
    态度层，**只能收紧、绝不放行前面任何一门**。
    """

    def evaluate(self, agent_context: AgentContext, tool: BaseTool) -> ExposureDecision:
        # G1 平台特权门（U2 加装）：required_scopes ∩ PRIVILEGED_SCOPES 未持有 → HIDDEN。

        # G2 来源接入门（硬边界）。
        # a) P7 第三方 MCP 网关：external 工具实例全局共享，发现/调用资格按本 Agent
        #    binding（gate1 owner 启用 + gate2 allowed_tools，由 server 每次调用前注入）
        #    per-request 过滤——不在授权集合的一律不可见，杜绝跨 Agent 串号。
        tool_name = getattr(tool, 'name', '')
        if getattr(tool, 'source', 'platform') == 'external':
            allowed = getattr(agent_context, 'external_allowed_tools', set()) or set()
            if tool_name not in allowed:
                return ExposureDecision(ACTION_HIDDEN, gate=GATE_SOURCE, reason=REASON_EXTERNAL_NOT_BOUND)
        # b) 运行位置收口（TOOLMIG2-P4）：本地分身在云端面隐藏 deck/task/workflow（其用
        #    本地面那份本地优先引擎），发现/执行两面一体（hasn.cloud.tool.call 透传经
        #    call_tool 重入一并拦住）。见 runtime_visibility。
        namespace = getattr(tool, 'namespace', None) or _fallback_namespace(tool_name)
        if is_namespace_hidden_for_runtime(namespace, getattr(agent_context, 'runtime_location', 'cloud')):
            return ExposureDecision(ACTION_HIDDEN, gate=GATE_SOURCE, reason=REASON_RUNTIME_HIDDEN)

        # G3 应用权益门（U3 加装）：tool.app_id 空间权益不通 → VISIBLE_DENY(need_*)。
        # G4 企业角色门（U4 加装）：企业空间角色未授予 → HIDDEN(role)。

        # G5 主人三态门（维度①唯一权威·最后态度层）：deny→隐身；ask→挂审批；allow→放行。
        # 维度② 对象可达性不在管线里，由工具运行时返回。
        mode = agent_context.tool_mode(tool)
        if mode == MODE_DENY:
            return ExposureDecision(ACTION_HIDDEN, gate=GATE_OWNER, reason=REASON_OWNER_DENIED)
        if mode == MODE_ASK:
            return DECISION_ASK
        return DECISION_ALLOW


def _fallback_namespace(tool_name: str) -> str:
    """无 namespace 属性的鸭子对象兜底，口径与 BaseTool.namespace 默认一致。"""
    parts = tool_name.split('.')
    if len(parts) < 2:
        return tool_name
    if tool_name.startswith('hasn.ext.') and len(parts) >= 3:
        return '.'.join(parts[:3])
    return '.'.join(parts[:2])


tool_exposure_policy = ToolExposurePolicy()
