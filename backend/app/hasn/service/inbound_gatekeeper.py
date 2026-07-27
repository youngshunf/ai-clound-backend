"""入站消息门控（外部 → Agent 全门控）。

事实源：docs/hasn-node设计文档/05-安全与权限/06-入站消息门控与抑制箱(外部→Agent全门控).md

职责：外部（人/别的分身）发给某个 Agent 的消息，在投递+唤醒 runtime 之前过一道门控。
任一闸门未过 → 不静默丢弃，而是**记录进主人的抑制箱**（hasn_suppressed_messages，
visible_to_owner=true），由主人审阅后放行或忽略。

五闸（短路求值，首个未过决定 suppress_reason）：
  1. agent.status != active            → agent_frozen
  2. agent.social_enabled == False     → social_disabled
  3. 黑名单（contact.status=blocked 或 trust_level=0） → reject_silent（D1：不进抑制箱）
  4. 权限矩阵 send_message == DENY      → permission_denied
  5. owner 策略 inbound_policy 命中     → manual_only
  否则 → allow（继续 route_message 既有流程：permission_engine 做铁律/限流/承诺/敏感）。

仅对「接收方是 Agent 且发送方非其 Owner、且是普通消息（非系统/通知）」启用——Owner→自己分身
由 iron_law_2 直放，不进本门控。限流（abuse_restricted）按 D3「设计完整、实施分期」由既有
permission_engine 的 iron_law_6 在 route_message DENY 路径落抑制箱，本模块不重复计数。
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass, field
from typing import Any, cast

import sqlalchemy as sa

from sqlalchemy import select

from backend.app.hasn.constants import DENY, get_default_permissions
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.service.effective_relation import (
    BLOCKED,
    DELIVER,
    REQUEST_AND_SUPPRESS,
    resolve_effective_relation,
)
from backend.common.log import log
from backend.database.schema_names import SCHEMA_NAMES

# 门控动作
ALLOW = 'allow'
SUPPRESS = 'suppress'
REJECT_SILENT = 'reject_silent'


@dataclass(frozen=True)
class GateOutcome:
    """门控判决（immutable）。action=allow 放行；suppress 入抑制箱；reject_silent 静默拒绝。"""

    action: str
    reason: str | None = None  # suppress_reason（仅 suppress/reject_silent）
    snapshot: dict[str, Any] = field(default_factory=dict)  # 门控上下文（脱敏后供主人理解）


@dataclass(frozen=True)
class _AgentGateState:
    """消息门控所需的身份状态与 IM 通信设置。"""

    status: str
    social_enabled: bool
    inbound_policy: str


async def _load_agent(db: Any, hasn_id: str) -> _AgentGateState | None:
    """读取身份存活态与 IM 权威通信设置，不再依赖身份表兼容列。"""
    settings_table = SCHEMA_NAMES.im_table('agent_communication_settings')
    row = (
        await db.execute(
            sa.text(
                f"""
                SELECT agent.status,
                       COALESCE(settings.social_enabled, true) AS social_enabled,
                       COALESCE(settings.inbound_policy, 'auto') AS inbound_policy
                FROM public.hasn_agents AS agent
                LEFT JOIN {settings_table} AS settings
                  ON settings.agent_hasn_id = agent.hasn_id
                WHERE agent.hasn_id = :hasn_id
                """  # noqa: S608 表名来自进程固定 schema 配置
            ),
            {'hasn_id': hasn_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    return _AgentGateState(
        status=row['status'],
        social_enabled=bool(row['social_enabled']),
        inbound_policy=str(row['inbound_policy'] or 'auto'),
    )


async def _load_contact(db: Any, owner_id: str, peer_id: str) -> HasnContacts | None:
    """接收方 Agent 的 Owner 视角下、对发送方 peer 的联系人记录（决定信任等级/黑名单）。"""
    result = await db.execute(
        select(HasnContacts).where(
            HasnContacts.owner_id == owner_id,
            HasnContacts.peer_id == peer_id,
        )
    )
    return result.scalar_one_or_none()


async def _agent_owner(db: Any, agent_id: str) -> str | None:
    """解析分身的主人 hasn_id（§4.1.1 主人派生的第一步）。非分身/查无返回 None。"""
    if not agent_id.startswith('a_'):
        return None
    result = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == agent_id))
    return result.scalar_one_or_none()


async def _materialize_edge(
    db: Any, *, owner_id: str, peer_agent: str, peer_owner_id: str | None, trust_level: int,
) -> None:
    """自动物化 `A→对方分身` 一条 social 边（trust 继承主人档，D7）→ 后续同 peer 走直连快路。

    协议 §7.6 继承规则的**入站对称**落地（出站侧 daemon 已有同规则）。用 UPSERT 兜历史行，
    add_source='auto_materialized' 标注来源，供审计区分「主人手动加」与「派生自动加」。
    """
    from backend.app.hasn_im.application.provider import (
        get_transactional_relation_gateway,
    )

    if not peer_owner_id:
        raise ValueError('自动物化关系缺少对方分身主人')
    gateway = get_transactional_relation_gateway(db)
    await gateway.materialize_derived_agent(
        owner_hasn_id=owner_id,
        peer_agent_hasn_id=peer_agent,
        peer_owner_hasn_id=peer_owner_id,
        trust_level=trust_level,
    )


async def _auto_first_contact_request(
    db: Any, *, from_agent: str, receiver_target: str, receiver_owner: str, receiver_type: str,
) -> int | None:
    """普通朋友(=2)首联 → 自动代发一条好友请求（幂等复用，§4.1.1 step 2）。

    请求方 from_id = **发送分身本体**（与暂存消息的 from_id 一致，放行/联系人页 accept 才能按
    同一 peer 反向联动重投）；审批人 to_owner_id = 接收方主人 A。已有 pending 幂等返回其 id
    （partial unique 索引 + 应用层去重双保险，绝不重复建）。关系事实与抑制命令共用 IM 事务，
    任一步失败都整体回滚，禁止产生无法追溯的半状态。
    """
    from backend.app.hasn_im.application.provider import (
        get_transactional_relation_gateway,
    )

    gateway = get_transactional_relation_gateway(db)
    return await gateway.ensure_auto_first_contact_request(
        from_agent_hasn_id=from_agent,
        receiver_hasn_id=receiver_target,
        receiver_owner_hasn_id=receiver_owner,
        receiver_type=receiver_type,
    )


async def _derive_via_owner(
    db: Any,
    *,
    receiver_owner_id: str,
    receiver_target: str,
    from_agent: str,
    receiver_entity_type: str,
) -> GateOutcome | None:
    """无直连实体边时，解析发送分身的主人派生有效关系（§4.1.1 修 B9 核心）。

    返回：
        GateOutcome(ALLOW)         ≥3 好友/密友 → 已物化 `A→分身` 边，直接送达；
        GateOutcome(SUPPRESS,...)  =2 普通朋友 → 已代发好友请求（id 落 snapshot），暂存拦截箱；
        GateOutcome(REJECT_SILENT) 主人边黑名单 → 静默拒；
        None                       陌生人/无边 → 交回调用方走现状陌生人门控。

    receiver_target：代发好友请求时的目标寻址（A2A=接收分身 hasn_id；A2H=主人 hasn_id）。
    """
    owner_b = await _agent_owner(db, from_agent)
    # 无主人（异常）或对方分身就是自有分身（owner loopback）→ 不派生，交现状
    if not owner_b or owner_b == receiver_owner_id:
        return None

    owner_edge = await _load_contact(db, receiver_owner_id, owner_b)
    owner_trust = owner_edge.trust_level if owner_edge and owner_edge.trust_level is not None else None
    owner_blocked = owner_edge is not None and (owner_edge.status == 'blocked' or owner_trust == 0)

    verdict = resolve_effective_relation(
        from_is_agent=True,
        direct_edge_trust=None,
        owner_edge_trust=owner_trust,
        owner_edge_blocked=owner_blocked,
    )
    decision = verdict['decision']

    if decision == BLOCKED:
        return GateOutcome(REJECT_SILENT, 'permission_denied', {'reason': 'owner_blocked'})

    if decision == DELIVER:
        inherit_trust = cast(int | None, verdict['inherit_trust']) or 0
        await _materialize_edge(
            db, owner_id=receiver_owner_id, peer_agent=from_agent,
            peer_owner_id=owner_b, trust_level=inherit_trust,
        )
        return GateOutcome(ALLOW, None, {'derived_from_owner': True, 'inherit_trust': inherit_trust})

    if decision == REQUEST_AND_SUPPRESS:
        req_id = await _auto_first_contact_request(
            db, from_agent=from_agent, receiver_target=receiver_target,
            receiver_owner=receiver_owner_id, receiver_type=receiver_entity_type,
        )
        return GateOutcome(
            SUPPRESS,
            'permission_denied',
            {
                'derived_from_owner': True,
                'pending_request_id': req_id,
                'relation_type': 'social',
                'trust_level': 2,
            },
        )

    # GATE：陌生人/无边 → 交回现状门控
    return None


async def evaluate_a2h_inbound(
    db: Any,
    *,
    from_agent: str,
    human_id: str,
    relation_type: str = 'social',
) -> GateOutcome:
    """分身→人类(A2H) 接收侧有效关系解析（§4.1.3 修 B12 后半）。

    与「发给我的分身」完全一致：好友直达 / 普通朋友转请求进箱 / 陌生人门控进箱 / 黑名单静默。
    调用方（message_router）据 action 处置——ALLOW 覆盖出站关系门 DENY 放行；SUPPRESS 落主人
    拦截箱（不再裸 2002 让主人看不到）；REJECT_SILENT 静默拒。fail-closed：异常按 SUPPRESS 暂存。
    """
    try:
        # owner loopback：分身发给自己主人 → 直放（本不经此，双保险）
        owner_b = await _agent_owner(db, from_agent)
        if owner_b and owner_b == human_id:
            return GateOutcome(ALLOW)

        # 直连边 A(human)→b_agent
        contact = await _load_contact(db, human_id, from_agent)
        if contact is not None:
            trust = contact.trust_level if contact.trust_level is not None else 1
            if contact.status == 'blocked' or trust == 0:
                return GateOutcome(REJECT_SILENT, 'permission_denied', {'reason': 'blocked'})
            if trust >= 2:
                return GateOutcome(ALLOW)  # 主人对该分身已设档≥2 → 直达
            # 直连=1 陌生档 → 门控进箱
            return GateOutcome(
                SUPPRESS, 'permission_denied',
                {'relation_type': contact.relation_type or 'social', 'trust_level': trust},
            )

        # 无直连边 → 主人派生（好友直达/普通朋友转请求进箱/黑名单静默）
        derived = await _derive_via_owner(
            db, receiver_owner_id=human_id, receiver_target=human_id,
            from_agent=from_agent, receiver_entity_type='human',
        )
        if derived is not None:
            return derived

        # 派生未命中（陌生人/无边）→ 门控进箱（关键：不再裸 2002）
        return GateOutcome(
            SUPPRESS, 'permission_denied',
            {'relation_type': 'social', 'trust_level': 1, 'stranger': True},
        )
    except Exception as exc:  # fail-closed：宁可暂留主人拦截箱，不误发也不静默丢
        log.warning(f'[a2h_gate] evaluate 异常, fail-closed suppress: {exc}')
        return GateOutcome(SUPPRESS, 'permission_denied', {'decision_reason': 'gate_error'})


async def evaluate_inbound(
    db: Any,
    *,
    from_id: str,
    agent_info: dict[str, Any],
    relation_type: str = 'social',
) -> GateOutcome:
    """对外部→Agent 的入站消息按五闸短路求值，返回门控判决。

    agent_info：resolve_target 返回的目标信息（含 hasn_id / owner_id）。
    fail-closed：判定中途异常 → suppress(permission_denied)（宁可暂留待主人放行，不误投）。
    """
    to_id = agent_info.get('hasn_id', '')
    owner_id = agent_info.get('owner_id')

    if not to_id or not owner_id:
        return GateOutcome(
            SUPPRESS,
            'permission_denied',
            {'decision_reason': 'gate_error'},
        )

    # 0. Owner 直放（route 已过滤，双保险）
    if owner_id and from_id == owner_id:
        return GateOutcome(ALLOW)

    try:
        agent = await _load_agent(db, to_id)
        if agent is None:
            # resolve_target 后并发删除也必须 fail-closed，禁止把不存在身份误判为可投递。
            return GateOutcome(
                SUPPRESS,
                'permission_denied',
                {'decision_reason': 'gate_error'},
            )

        # 1. 状态/冻结：非 active（disabled/revoked/archived，未来 frozen）→ 不唤醒，暂留待恢复
        if agent.status and agent.status != 'active':
            return GateOutcome(SUPPRESS, 'agent_frozen', {'status': agent.status})

        # 2. 社交开启：未开社交 → 仅记录不唤醒（Core/03 §3.2）
        if agent.social_enabled is False:
            return GateOutcome(SUPPRESS, 'social_disabled', {})

        # 3. 联系人/黑名单/有效关系解析（§4.1.1 修 B9）
        contact = await _load_contact(db, owner_id, from_id) if owner_id else None

        # 3.1 无直连实体边且发送方是分身 → 解析其主人派生（≥3 物化直达 / =2 代发请求进箱 /
        #     黑名单静默）。派生未命中（陌生人/无边）返回 None，落回下方现状陌生人门控。
        if contact is None and owner_id and from_id.startswith('a_'):
            derived = await _derive_via_owner(
                db, receiver_owner_id=owner_id, receiver_target=to_id,
                from_agent=from_id, receiver_entity_type='agent',
            )
            if derived is not None:
                return derived

        eff_relation = (contact.relation_type if contact else None) or relation_type or 'social'
        # 无联系人 = 陌生人（trust_level=1）；有则取其信任等级
        trust_level = contact.trust_level if contact and contact.trust_level is not None else 1
        is_blocked = contact is not None and (contact.status == 'blocked' or trust_level == 0)
        if is_blocked:
            # D1：黑名单静默拒绝，不进抑制箱（避免「拉黑了还天天看到对方想联系」）
            return GateOutcome(REJECT_SILENT, 'permission_denied', {'reason': 'blocked'})

        # 4. 权限矩阵：send_message 决策（陌生人/低信任 social 默认 DENY）
        perms = get_default_permissions(eff_relation, trust_level)
        decision = perms.get('send_message', DENY)
        if decision == DENY:
            return GateOutcome(
                SUPPRESS,
                'permission_denied',
                {
                    'decision_reason': 'matrix_constrained',
                    'relation_type': eff_relation,
                    'trust_level': trust_level,
                },
            )

        # 5. manual_only 策略（应用层）：主人设了「外部消息先暂留手动放行」
        policy = getattr(agent, 'inbound_policy', 'auto') or 'auto'
        is_stranger = contact is None or trust_level < 2
        if policy == 'manual_all' or (policy == 'manual_strangers' and is_stranger):
            return GateOutcome(SUPPRESS, 'manual_only', {'inbound_policy': policy})

        # 6. 放行
        return GateOutcome(ALLOW)

    except Exception as exc:  # fail-closed：宁可暂留，不误投
        log.warning(f'[inbound_gate] evaluate 异常, fail-closed suppress: {exc}')
        return GateOutcome(SUPPRESS, 'permission_denied', {'decision_reason': 'gate_error'})


async def record_suppression(
    db: Any,
    *,
    owner_id: str,
    hasn_id: str,
    conversation_id: str,
    sender_hasn_id: str,
    idempotency_scope: str,
    command_hash: str,
    command_payload: dict[str, Any],
    reason: str,
    policy_snapshot: dict[str, Any] | None = None,
    dispatch_status: str = 'suppressed_by_policy',
) -> dict[str, Any]:
    """把待放行发送命令写入抑制箱，不创建消息、不占用会话序号。

    ``idempotency_scope`` 唯一约束保证同发送主体、来源节点和幂等键只产生一条抑制命令。
    同键不同 ``command_hash`` 显式报冲突，禁止覆盖已待主人审核的正文。
    """
    suppressed_table = SCHEMA_NAMES.im_table('hasn_suppressed_messages')
    inserted = (
        await db.execute(
            sa.text(
                f"""
                INSERT INTO {suppressed_table} (
                    message_id, owner_id, hasn_id, conversation_id,
                    sender_hasn_id, idempotency_scope, command_hash, command_payload,
                    suppress_reason, dispatch_status, policy_snapshot,
                    runtime_summary, visible_to_owner, created_time
                ) VALUES (
                    NULL, :owner_id, :hasn_id, CAST(:conversation_id AS uuid),
                    :sender_hasn_id, :idempotency_scope, :command_hash,
                    CAST(:command_payload AS jsonb),
                    :suppress_reason, :dispatch_status, CAST(:policy_snapshot AS jsonb),
                    CAST('{{}}' AS jsonb), true, now()
                )
                ON CONFLICT (idempotency_scope)
                    WHERE idempotency_scope IS NOT NULL
                DO NOTHING
                RETURNING id, message_id, command_hash, resolved_at
                """  # noqa: S608 表名来自进程固定的 schema 配置
            ),
            {
                'owner_id': owner_id,
                'hasn_id': hasn_id,
                'conversation_id': conversation_id,
                'sender_hasn_id': sender_hasn_id,
                'idempotency_scope': idempotency_scope,
                'command_hash': command_hash,
                'command_payload': json.dumps(
                    command_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(',', ':'),
                ),
                'suppress_reason': reason,
                'dispatch_status': dispatch_status,
                'policy_snapshot': json.dumps(policy_snapshot or {}, ensure_ascii=False),
            },
        )
    ).mappings().one_or_none()
    if inserted is not None:
        return dict(inserted)

    existing = (
        await db.execute(
            sa.text(
                f"""
                SELECT id, message_id, command_hash, resolved_at
                FROM {suppressed_table}
                WHERE idempotency_scope = :idempotency_scope
                FOR UPDATE
                """  # noqa: S608 表名来自进程固定的 schema 配置
            ),
            {'idempotency_scope': idempotency_scope},
        )
    ).mappings().one()
    if existing['command_hash'] != command_hash:
        raise ValueError('同一幂等键已被不同的抑制消息命令使用')
    return dict(existing)


def suppression_command_identity(
    *,
    sender_hasn_id: str,
    origin_node_id: str | None,
    idempotency_key: str,
    command_payload: dict[str, Any],
) -> tuple[str, str]:
    """计算抑制命令唯一作用域和载荷指纹。"""
    scope_source = '\0'.join(
        (sender_hasn_id, origin_node_id or '', idempotency_key)
    )
    canonical_payload = json.dumps(
        command_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return (
        hashlib.sha256(scope_source.encode()).hexdigest(),
        hashlib.sha256(canonical_payload.encode()).hexdigest(),
    )
