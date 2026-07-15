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

    from backend.app.hasn.model.hasn_agents import HasnAgents
    from backend.app.hasn.model.hasn_contacts import HasnContacts
    from backend.app.hasn.service.effective_relation import (
        DELIVER,
        resolve_effective_relation,
    )

    if not owner_hasn_id:
        return None

    # ① 直连实体边优先（尊重主人对该 peer 的显式设档）。
    direct = (
        await db.execute(
            select(HasnContacts).where(
                HasnContacts.owner_id == owner_hasn_id,
                HasnContacts.peer_id == peer_id,
            )
        )
    ).scalar_one_or_none()
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
    owner_edge = (
        await db.execute(
            select(HasnContacts).where(
                HasnContacts.owner_id == owner_hasn_id,
                HasnContacts.peer_id == peer_owner,
            )
        )
    ).scalar_one_or_none()
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
