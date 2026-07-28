"""L3 工具门（doc08 §4·RT3）云端半场：对外会话「能力调用类工具按信任档硬门控」。

事实源：docs/hasn-node设计文档/05-安全与权限/08-关系类型与信任等级完整定义与实现.md §4 / §9 RT3。

「信任等级生效」不是一个闸、而是四层执行面各管一段（doc08 §4）：L1 注入（软提示）、L2 出站
披露裁判（doc07 三层漏斗）、L4 入站消息门（云端五闸）都已闭环，**L3 工具门**是对方问「他明天
有空吗」时、分身在对外会话里调 `hasn.plan.*` 读日程没有按档拦截的那道缺口。本地 hasn-mcp 的
L3 门（``crates/hasn-mcp/src/trust_gate.rs``）已闭环，但真正敏感的读工具（看日程/看计划/偏好/
位置）已迁为**云端平台工具**，经 ``/api/v1/mcp/streamable`` 直达、绕过本地 resolver，本地门覆盖
不到。本模块补云端同款按档硬门控——判定口径逐条对齐本地 ``trust_gate.rs``。

三条不变量（与 hasn-node ``trust_gate.rs`` 逐条同款）：
- **主会话不受限**：owner ↔ 自己分身（OwnerLoopback，``is_external=False``）永远放行。
- **群取 roster 最低档**：群会话 ``peer_trust`` 由 daemon 填 roster 最低档（与 L2 disclosure
  同口径），判定逻辑与 1:1 完全一致。
- **fail-closed**：对外会话里 ``peer_trust`` 缺失当**最低档（陌生人 1）**处理，宁拒不漏。

⚠️ **会话信任语境由系统注入、分身不可伪造**：本次是否对外会话 + 对端 peer/档由 daemon 派发时
**戳进保留参数**（``RESERVED_*``，runtime 在 LLM 之后无条件覆盖模型产出的同名键——模型永远无法
伪造），云端 dispatch 前**剥离**保留参数、按对端**真实** trust（复用 RT1.5 ``effective_relation``
自云端权威 ``hasn_contacts`` 解析）判档，再放行/结构化拒绝。
"""

from __future__ import annotations

import json
import re

from typing import Any

from backend.app.mcp.errors import McpErrorCode, McpToolError

# ── 系统注入的会话信任语境保留参数（分身永不该见到，dispatch 前剥离）──────────────
# 与 hasn-node 本地 key 承载的 (is_external_conversation, peer_id, peer_trust) 一一对应：
#   _hasn_is_external：本次是否对外会话（缺省/False = 主会话，不受 L3 门约束）。
#   _hasn_peer_id：1:1 对端实体 hasn_id——云端据此解析对端**真实** trust（权威在云端 hasn_contacts）。
#   _hasn_peer_trust：daemon 预解析的对端档（群 roster 最低档 / 快路），无 peer_id 时回落用。
RESERVED_IS_EXTERNAL = '_hasn_is_external'
RESERVED_PEER_ID = '_hasn_peer_id'
RESERVED_PEER_TRUST = '_hasn_peer_trust'
_RESERVED_KEYS = (RESERVED_IS_EXTERNAL, RESERVED_PEER_ID, RESERVED_PEER_TRUST)

# ── 系统注入的工作会话 id 保留参数（register-on-write，doc31/32 RC-P8 泛化）──────────
# 分身经工作会话派发时，Hermes/daemon 在每次出站 MCP 调用后无条件戳进此保留参数（分身不可伪造、
# 工具体不该见）。云端 dispatch 前剥离 → 落 ``AgentContext.session_id``，供 deck/app 写点把
# 产出登记进「工作会话资源栏 / 分身产物 tab」。缺省（主会话直调 / 非工作会话）→ None，产物仍凭
# resource_uri 归位、进产物 tab，只是不额外挂到某工作会话资源栏。
RESERVED_SESSION_ID = '_hasn_session_id'

# ── 系统注入的**工作会话** id 保留参数（设计 02 §4.3 会话轴分流）───────────────────
# 与 `_hasn_session_id` 严格分工：`_hasn_session_id` 恒为运行时/逻辑会话语义（IM 续接、
# message.send 的 origin 回灌定位），本键只承载**真实工作会话派发**（任务/appcollab）的工作会话
# id——新节点（CLI per-dispatch key / 升级后 Hermes fork）双键同发、本键仅在非空时盖章；
# IM 主会话派发缺省。云端 dispatch 前剥离 → 落 work_session ContextVar（register-on-write
# 挂「工作会话资源栏」的唯一权威来源）。缺省时分发入口对 `_hasn_session_id` 做「在册 task
# 收窄」兼容旧节点（详见 server.py 提取点），IM runtime id 不落 ContextVar。
RESERVED_WORK_SESSION_ID = '_hasn_work_session_id'

# ── 系统注入的平台项目 id 保留参数（doc38 §3.3·第三条轴「为了哪件事」联邦挂靠）───────────
# 与 `_hasn_session_id` 完全同管道：分身经项目上下文派发时，daemon 在每次出站 MCP 调用后无条件
# 戳进此保留参数（分身不可伪造、工具体不该见）。云端 dispatch 前剥离 → 落 ContextVar，供 register-on-write
# 公共接缝把产物自动打标进 ``hasn_artifacts.project_id``（只进不退）。缺省（不在项目中工作）→ None，
# 产物仍凭 resource_uri 归位、进产物 tab，只是不额外挂到某项目。
RESERVED_PROJECT_ID = '_hasn_project_id'

# ── 系统注入的工作会话精确工具白名单 ────────────────────────────────────────
# Hermes 在模型输出之后把该 JSON 数组逐调用盖章；CLI runtime 则经可信 Header 进入同一
# AgentContext。None（字段缺失）与 []（拒绝全部业务工具）必须保持可区分。
RESERVED_ALLOWED_TOOL_NAMES = '_hasn_allowed_tool_names'
_CANONICAL_TOOL_NAME = re.compile(r'^hasn(?:\.[A-Za-z0-9_-]+)+$')
_SESSION_TRANSPORT_TOOLS = frozenset({
    'hasn.local.tool.search',
    'hasn.local.tool.call',
    'hasn.cloud.tool.search',
    'hasn.cloud.tool.call',
    'hasn.tool.search',
})

# 系统注入保留字段的公共前缀：三族（`_hasn_session_id` / `_hasn_project_id` 与 `_hasn_is_external` /
# `_hasn_peer_id` / `_hasn_peer_trust`）都在此命名空间下，wire 上按前缀整族放行。
_RESERVED_FIELD_PATTERN = '^_hasn_'


def allow_reserved_fields_in_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把工具 input_schema 投影到 MCP SDK 前，先让它在 wire 上容得下 `_hasn_*` 保留字段。

    **为什么非在这一层开口不可**：MCP SDK 在把请求交给我们的 ``call_tool`` 之前，就已经拿工具
    声明的 ``inputSchema`` 校验了**原始 wire 入参**（``mcp/server/lowlevel/server.py`` 的
    ``jsonschema.validate``）；而保留字段是 Hermes 出站时**逐调用**打进入参的（MCP 连接长活、
    会话逐调用变，只能走入参、不能像 CLI runtime 那样走 ``X-Hasn-*`` header），我们的剥离发生
    在 SDK 校验**之后**。于是任何声明 ``additionalProperties: false`` 的工具都会被 SDK 判
    ``Additional properties are not allowed ('_hasn_session_id' was unexpected)`` 直接拒掉——
    ``hasn.cloud.tool.search`` 首当其冲：分身连工具都发现不了，连续失败还会触发 Runtime 侧的
    MCP 熔断，把整个 cloud server 判为不可达，全线工具跟着雪崩。

    做法是给严格 schema 补一条 ``patternProperties: {"^_hasn_": {}}``：JSON Schema 里
    ``additionalProperties`` 只管 ``properties`` / ``patternProperties`` **都没匹配上**的键，
    故保留命名空间被放行、其余未知键照旧严格拒绝（**严格度零损失**）。``properties`` 不动——
    保留字段仍不在工具对外声明的字段表里，分身读 schema 看不到它们。

    只处理顶层：保留字段只打在 arguments 顶层；``additionalProperties`` 非 ``False`` 的 schema
    本就放行未知键，原样返回不动。**返回新对象**，绝不改原 schema（工具的 input_schema 常是
    模块级字面量，就地改会污染全局）。
    """
    if not isinstance(schema, dict) or schema.get('additionalProperties') is not False:
        return schema
    existing = schema.get('patternProperties')
    patterns = dict(existing) if isinstance(existing, dict) else {}
    if _RESERVED_FIELD_PATTERN in patterns:
        return schema
    patterns[_RESERVED_FIELD_PATTERN] = {}
    return {**schema, 'patternProperties': patterns}


# fail-closed 兜底档：对外会话里 peer_trust 缺失时按**陌生人(1)** 判定（宁拒不漏）。
# 不用 0（黑名单——那是「显式拉黑」语义）；缺失只是「未知」，按陌生人已足够严。
FAIL_CLOSED_TRUST_LEVEL = 1

# 普通朋友档：矩阵起点档（与 effective_relation.NORMAL_TRUST_LEVEL 同值）。
_NORMAL_TRUST_LEVEL = 2
# 好友档下限：≥3 才自动物化并继承主人档（与 effective_relation.FRIEND_TRUST_FLOOR 同值）。
_FRIEND_TRUST_FLOOR = 3

# 信任档(0-5) → 产品显示名（与 hasn-node trust_gate.rs::trust_level_label 逐条一致，
# 与 UI / 裁判 / 提示词三处用词统一）。越界（不该出现）诚实回落「未知」，不臆造。
_TRUST_LABELS = {0: '黑名单', 1: '陌生人', 2: '普通朋友', 3: '好友', 4: '密友', 5: '主人'}


def trust_level_label(level: int) -> str:
    """信任档 → 产品显示名（越界回落「未知」）。"""
    return _TRUST_LABELS.get(level, '未知')


def evaluate_min_trust_level(
    min_trust_level: int | None,
    peer_trust: int | None,
    *,
    is_external: bool,
) -> None:
    """L3 工具门核心判定（纯函数·零副作用·零 IO，便于单测）。

    放行条件：无对外门 ∨ 非对外会话 ∨ 当前档 ≥ 所需档；否则结构化拒绝
    （``McpToolError`` TRUST_LEVEL_INSUFFICIENT，文案含当前档 + 所需档 + 回绝引导，分身据此
    礼貌回绝对方）。

    Raises:
        McpToolError: 对外会话里当前档低于工具所需档（code=TRUST_LEVEL_INSUFFICIENT）。
    """
    # 工具无对外门（未声明 min_trust_level）→ 任何会话都放行。
    if min_trust_level is None:
        return
    # 主会话（owner ↔ 自己分身，OwnerLoopback）不受 L3 门约束——主人看自己日程永远放行。
    if not is_external:
        return
    # 对外会话：peer_trust 缺失 fail-closed 当最低档（陌生人 1）。
    current = peer_trust if peer_trust is not None else FAIL_CLOSED_TRUST_LEVEL
    if current >= min_trust_level:
        return
    raise McpToolError(
        McpErrorCode.TRUST_LEVEL_INSUFFICIENT,
        f'该操作需要「{trust_level_label(min_trust_level)}({min_trust_level})」及以上的信任关系，'
        f'当前对方为「{trust_level_label(current)}({current})」，已礼貌回绝——请客气地告知对方'
        f'此事需要更亲近的关系或主人本人授权，不要执行该操作。',
    )


def pop_trust_context(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], bool, str | None, int | None]:
    """从工具入参剥离系统注入的会话信任语境保留参数（分身永不该见到它们）。

    返回 ``(cleaned_args, is_external, peer_id, peer_trust)``。无任何保留参数 → 原样返回 +
    ``is_external=False``（主会话，never over-block：绝不因缺语境而误伤主人自己的调用）。
    """
    if not any(k in arguments for k in _RESERVED_KEYS):
        return arguments, False, None, None
    cleaned = {k: v for k, v in arguments.items() if k not in _RESERVED_KEYS}
    is_external = bool(arguments.get(RESERVED_IS_EXTERNAL))
    raw_peer = arguments.get(RESERVED_PEER_ID)
    peer_id = str(raw_peer) if raw_peer else None
    peer_trust = _coerce_trust(arguments.get(RESERVED_PEER_TRUST))
    return cleaned, is_external, peer_id, peer_trust


def pop_session_id(arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """从工具入参剥离系统注入的工作会话 id 保留参数（``_hasn_session_id``）。

    返回 ``(cleaned_args, session_id)``。无该参数 → 原样返回 + ``None``（never over-block：
    缺会话 id 只是不把产物额外挂进某会话资源栏，绝不影响工具执行、也不影响产物按 resource_uri 归位）。
    """
    if not isinstance(arguments, dict) or RESERVED_SESSION_ID not in arguments:
        return arguments, None
    cleaned = {k: v for k, v in arguments.items() if k != RESERVED_SESSION_ID}
    raw = arguments.get(RESERVED_SESSION_ID)
    sid = str(raw).strip() if raw is not None else ''
    return cleaned, (sid or None)


def pop_work_session_id(arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """从工具入参剥离系统注入的**工作会话** id 保留参数（``_hasn_work_session_id``，设计 02 §4.3）。

    返回 ``(cleaned_args, work_session_id)``。无该参数 → 原样返回 + ``None``（never over-block：
    缺工作会话 id 只是不把产物额外挂进某会话资源栏，绝不影响工具执行、也不影响产物按
    resource_uri 归位）。与 ``pop_session_id`` 同形状——同一分发入口先后各剥离一次。
    """
    if not isinstance(arguments, dict) or RESERVED_WORK_SESSION_ID not in arguments:
        return arguments, None
    cleaned = {k: v for k, v in arguments.items() if k != RESERVED_WORK_SESSION_ID}
    raw = arguments.get(RESERVED_WORK_SESSION_ID)
    sid = str(raw).strip() if raw is not None else ''
    return cleaned, (sid or None)


def pop_project_id(arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """从工具入参剥离系统注入的平台项目 id 保留参数（``_hasn_project_id``）。

    返回 ``(cleaned_args, project_id)``。无该参数 → 原样返回 + ``None``（never over-block：
    缺项目 id 只是不把产物额外打标进某项目，绝不影响工具执行、也不影响产物按 resource_uri 归位）。
    与 ``pop_session_id`` 同形状——同一分发入口先后各剥离一次。
    """
    if not isinstance(arguments, dict) or RESERVED_PROJECT_ID not in arguments:
        return arguments, None
    cleaned = {k: v for k, v in arguments.items() if k != RESERVED_PROJECT_ID}
    raw = arguments.get(RESERVED_PROJECT_ID)
    pid = str(raw).strip() if raw is not None else ''
    return cleaned, (pid or None)


def parse_allowed_tool_names(raw: Any) -> frozenset[str]:
    """严格解析系统注入的会话工具白名单。

    只接受无重复的 canonical 字符串数组；任何形状错误都以 MCP_9206 失败关闭。
    """
    if not isinstance(raw, list):
        raise McpToolError(McpErrorCode.TOOL_NOT_ALLOWED, '工作会话工具白名单必须是字符串数组')
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not _CANONICAL_TOOL_NAME.fullmatch(value):
            raise McpToolError(McpErrorCode.TOOL_NOT_ALLOWED, '工作会话工具白名单包含非法工具名')
        if value in names:
            raise McpToolError(McpErrorCode.TOOL_NOT_ALLOWED, f'工作会话工具白名单包含重复名称: {value}')
        names.append(value)
    return frozenset(names)


def apply_session_tool_allowlist_header(
    agent_context: Any,
    headers: dict[bytes, bytes],
) -> None:
    """把 CLI per-dispatch Header 严格解析进 AgentContext；缺失时保持 None。"""
    allowed_tools_header = headers.get(b'x-hasn-allowed-tools')
    if allowed_tools_header is None:
        return
    agent_context.allowed_tool_names = parse_allowed_tool_names(
        json.loads(allowed_tools_header.decode('utf-8'))
    )


def pop_allowed_tool_names(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], frozenset[str] | None]:
    """剥离 Runtime 在模型之后盖章的精确工具白名单；字段缺失返回 None。"""
    if not isinstance(arguments, dict) or RESERVED_ALLOWED_TOOL_NAMES not in arguments:
        return arguments, None
    cleaned = {key: value for key, value in arguments.items() if key != RESERVED_ALLOWED_TOOL_NAMES}
    return cleaned, parse_allowed_tool_names(arguments.get(RESERVED_ALLOWED_TOOL_NAMES))


def is_session_tool_allowed(agent_context: Any, tool_name: str) -> bool:
    """判断工具是否可在本次工作会话调用；渐进式传输包装器始终保留。"""
    if tool_name in _SESSION_TRANSPORT_TOOLS:
        return True
    allowed = getattr(agent_context, 'allowed_tool_names', None)
    return allowed is None or tool_name in allowed


def _coerce_bool(value: str | None) -> bool:
    """把 header 里的 is_external 原始字符串归一成 bool（``true``/``1``/``yes`` = True，余 False）。"""
    return (value or '').strip().lower() in ('true', '1', 'yes')


def read_header_trust_context() -> tuple[bool, str | None, int | None] | None:
    """读本次请求携带的会话信任语境 **header**（L3 门 header 优先来源）。

    CLI runtime（claude_code/codex）直连云端 ``streamable``、daemon 不在工具调用路径上，无法像
    reserved-arg 那样注入入参，故会话信任语境走 daemon 组装的 per-dispatch HTTP header
    （``X-Hasn-*``）下发——传输层已把原始三元组落进 ContextVar。

    返回 ``(is_external, peer_id, peer_trust)``；**无** header（非 CLI runtime / 未注入）→ ``None``，
    由 ``_enforce_conversation_trust_gate`` 回落工具入参保留参数（inert-safe：两者皆缺 = 主会话放行）。
    """
    from backend.app.mcp.context import get_trust_context_header

    raw = get_trust_context_header()
    if raw is None:
        return None
    is_external_raw, peer_id_raw, peer_trust_raw = raw
    is_external = _coerce_bool(is_external_raw)
    peer_id = peer_id_raw or None
    peer_trust = _coerce_trust(peer_trust_raw)
    return is_external, peer_id, peer_trust


def _coerce_trust(value: Any) -> int | None:
    """把保留参数里的 peer_trust 归一成 int（非法/缺失 → None，交门 fail-closed）。"""
    if isinstance(value, bool):  # bool 是 int 子类，先排除避免 True→1 误当档位
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    return None


async def resolve_conversation_peer_trust(
    db: Any, owner_hasn_id: str | None, peer_id: str
) -> int | None:
    """解析对端 peer 在本主人处的**真实**信任档（复用 RT1.5 ``effective_relation``·云端权威）。

    与 ``inbound_gatekeeper`` 同口径：
    - **直连边优先**：本主人对该 peer 实体的 ``hasn_contacts`` 边——黑名单(status=blocked
      或 trust_level=0)=0，否则取 ``trust_level``。
    - **无直连边且 peer 是分身**：走主人派生（本主人对 peer 主人的 social 边），交
      ``resolve_effective_relation`` 判——DELIVER 且继承档 ≥3 时返其继承档，否则 None。
    - **解析不到**返回 None（对外会话由门 fail-closed 当陌生人 1）。

    只读、不落库（判档快路，不做入站门控的物化/代发副作用）。
    """
    from sqlalchemy import select

    from backend.app.hasn_core import HasnAgents
    from backend.app.hasn.service.effective_relation import (
        DELIVER,
        resolve_effective_relation,
    )
    from backend.app.hasn_im.application.provider import get_relation_gateway

    if not owner_hasn_id:
        return None

    # ① 直连实体边优先（尊重主人对该 peer 的显式设档）。
    relation_gateway = get_relation_gateway()
    direct = await relation_gateway.resolve_effective_relation(
        owner_hasn_id=owner_hasn_id,
        peer_hasn_id=peer_id,
    )
    if direct is not None:
        trust = direct.trust_level if direct.trust_level is not None else _NORMAL_TRUST_LEVEL
        if direct.status == 'blocked' or trust == 0:
            return 0
        return int(trust)

    # ② 无直连边：仅 peer 是分身时做主人派生（人发的无边 = 纯陌生，返 None）。
    if not peer_id.startswith('a_'):
        return None
    peer_owner = (
        await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == peer_id))
    ).scalar_one_or_none()
    if not peer_owner:
        return None
    owner_edge = await relation_gateway.resolve_effective_relation(
        owner_hasn_id=owner_hasn_id,
        peer_hasn_id=peer_owner,
    )
    owner_trust = owner_edge.trust_level if owner_edge and owner_edge.trust_level is not None else None
    owner_blocked = owner_edge is not None and (owner_edge.status == 'blocked' or owner_trust == 0)

    verdict = resolve_effective_relation(
        from_is_agent=True,
        direct_edge_trust=None,
        owner_edge_trust=owner_trust,
        owner_edge_blocked=owner_blocked,
    )
    inherit = verdict.get('inherit_trust')
    if verdict.get('decision') == DELIVER and isinstance(inherit, int):
        return inherit
    # REQUEST_AND_SUPPRESS(=2 普通朋友) / GATE / BLOCKED → 尚未建立直达关系，判档不放宽（None）。
    return None
