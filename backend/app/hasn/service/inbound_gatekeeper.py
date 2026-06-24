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

import json

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from sqlalchemy import select

from backend.app.hasn.constants import DENY, get_default_permissions
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.common.log import log

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


async def _load_agent(db: Any, hasn_id: str) -> HasnAgents | None:
    result = await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
    return result.scalar_one_or_none()


async def _load_contact(db: Any, owner_id: str, peer_id: str) -> HasnContacts | None:
    """接收方 Agent 的 Owner 视角下、对发送方 peer 的联系人记录（决定信任等级/黑名单）。"""
    result = await db.execute(
        select(HasnContacts).where(
            HasnContacts.owner_id == owner_id,
            HasnContacts.peer_id == peer_id,
        )
    )
    return result.scalar_one_or_none()


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

    # 0. Owner 直放（route 已过滤，双保险）
    if owner_id and from_id == owner_id:
        return GateOutcome(ALLOW)

    try:
        agent = await _load_agent(db, to_id)
        if agent is None:
            # 目标不存在交给 resolve_target（已返回 None）；这里出现说明并发删除，放行交后续兜底
            return GateOutcome(ALLOW)

        # 1. 状态/冻结：非 active（disabled/revoked/archived，未来 frozen）→ 不唤醒，暂留待恢复
        if agent.status and agent.status != 'active':
            return GateOutcome(SUPPRESS, 'agent_frozen', {'status': agent.status})

        # 2. 社交开启：未开社交 → 仅记录不唤醒（Core/03 §3.2）
        if agent.social_enabled is False:
            return GateOutcome(SUPPRESS, 'social_disabled', {})

        # 3. 联系人/黑名单
        contact = await _load_contact(db, owner_id, from_id) if owner_id else None
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
    message_id: int,
    owner_id: str,
    hasn_id: str,
    conversation_id: str,
    reason: str,
    policy_snapshot: dict[str, Any] | None = None,
    dispatch_status: str = 'suppressed_by_policy',
) -> None:
    """把已落库的受抑制消息写入抑制箱（hasn_suppressed_messages，visible_to_owner=true）。

    与 hasn_message_hub_service.store_suppressed 同 SQL 形态（ON CONFLICT(message_id) 幂等）；
    入参为裸字段，供 route_message 的入站门控路径复用，不绑定 hub 的 StoredMessage。
    """
    await db.execute(
        sa.text(
            """
            INSERT INTO hasn_suppressed_messages (
                message_id, owner_id, hasn_id, conversation_id,
                suppress_reason, dispatch_status, policy_snapshot,
                runtime_summary, visible_to_owner, created_time
            ) VALUES (
                CAST(:message_id AS bigint), :owner_id, :hasn_id, CAST(:conversation_id AS uuid),
                :suppress_reason, :dispatch_status, CAST(:policy_snapshot AS jsonb),
                CAST('{}' AS jsonb), true, now()
            )
            ON CONFLICT (message_id) DO UPDATE SET
                suppress_reason = EXCLUDED.suppress_reason,
                dispatch_status = EXCLUDED.dispatch_status,
                policy_snapshot = EXCLUDED.policy_snapshot,
                visible_to_owner = true,
                updated_time = now()
            """
        ),
        {
            'message_id': message_id,
            'owner_id': owner_id,
            'hasn_id': hasn_id,
            'conversation_id': conversation_id,
            'suppress_reason': reason,
            'dispatch_status': dispatch_status,
            'policy_snapshot': json.dumps(policy_snapshot or {}, ensure_ascii=False),
        },
    )
