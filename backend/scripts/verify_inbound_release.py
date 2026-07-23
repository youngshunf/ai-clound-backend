"""入站门控放行（release_suppressed）真实验证（零 mock，连真实本地 PG）。

跑法：uv run python -m backend.scripts.verify_inbound_release
覆盖：permission_denied(persist→建联系人提信任) / social_disabled(persist→开社交) /
      agent_frozen(拒放行保留) / manual_only(once→仅出箱) / owner 隔离(not_found)。
临时数据用 _rl_ 前缀，跑前后自清理。
"""

import asyncio

from sqlalchemy import delete, select, text

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_suppressed_messages import HasnSuppressedMessages
from backend.app.hasn.service.inbound_gatekeeper import record_suppression
from backend.app.hasn.service.inbound_release import release_suppressed
from backend.app.hasn_im.application.message_service import get_or_create_conversation, persist_message
from backend.database.db import async_db_session

OWNER = 'h_rl_owner'
OTHER_OWNER = 'h_rl_other'
STRANGER = 'h_rl_stranger'


async def _cleanup(session) -> None:
    await session.execute(
        delete(HasnSuppressedMessages).where(HasnSuppressedMessages.owner_id.in_([OWNER, OTHER_OWNER]))
    )
    await session.execute(delete(HasnContacts).where(HasnContacts.owner_id == OWNER))
    await session.execute(
        text("DELETE FROM hasn_messages WHERE from_id = :s AND to_id LIKE 'a_rl_%'"),
        {'s': STRANGER},
    )
    await session.execute(
        text('DELETE FROM hasn_conversations WHERE participant_a_id = :s OR participant_b_id = :s'),
        {'s': STRANGER},
    )
    await session.execute(delete(HasnAgents).where(HasnAgents.owner_id == OWNER))
    await session.commit()


async def _mk_agent(session, hasn_id, *, status='active', social=True):
    session.add(
        HasnAgents(
            hasn_id=hasn_id,
            star_id=hasn_id.replace('a_rl_', 'rl') + '#star',
            owner_id=OWNER,
            display_name='放行测试分身',
            status=status,
            social_enabled=social,
        )
    )
    await session.commit()
    return {'hasn_id': hasn_id, 'owner_id': OWNER}


async def _seed_suppressed(session, agent, *, reason):
    """真实落一条受抑制消息（persist hasn_messages + 抑制箱行），返回 message_id。"""
    conv = await get_or_create_conversation(session, STRANGER, 'human', agent['hasn_id'], 'agent', 'social')
    msg = await persist_message(
        db=session,
        conversation_id=str(conv.id),
        from_id=STRANGER,
        to_id=agent['hasn_id'],
        content={'text': f'外部消息（{reason}）'},
        content_type=1,
        msg_type='message',
    )
    await record_suppression(
        session,
        message_id=msg.id,
        owner_id=OWNER,
        hasn_id=agent['hasn_id'],
        conversation_id=str(conv.id),
        reason=reason,
        policy_snapshot={'seed': reason},
    )
    await session.commit()
    return msg.id


async def _suppressed_exists(session, message_id):
    row = (
        await session.execute(select(HasnSuppressedMessages).where(HasnSuppressedMessages.message_id == message_id))
    ).scalar_one_or_none()
    return row is not None


async def main() -> None:
    results = []

    def check(name, cond, detail='') -> None:
        results.append((name, bool(cond), detail))

    async with async_db_session() as session:
        await _cleanup(session)

        # A. permission_denied + persist → 建联系人提信任(≥2) + 出箱
        a = await _mk_agent(session, 'a_rl_perm')
        mid = await _seed_suppressed(session, a, reason='permission_denied')
        out = await release_suppressed(session, owner_id=OWNER, message_id=mid, mode='persist')
        contact = (
            await session.execute(
                select(HasnContacts).where(HasnContacts.owner_id == OWNER, HasnContacts.peer_id == STRANGER)
            )
        ).scalar_one_or_none()
        check(
            'A permission_denied+persist',
            out.get('released') is True
            and not await _suppressed_exists(session, mid)
            and contact is not None
            and contact.trust_level >= 2,
            f'released={out.get("released")} trust={getattr(contact, "trust_level", None)}',
        )

        # B. social_disabled + persist → 开社交 + 出箱
        b = await _mk_agent(session, 'a_rl_social', social=False)
        mid = await _seed_suppressed(session, b, reason='social_disabled')
        out = await release_suppressed(session, owner_id=OWNER, message_id=mid, mode='persist')
        agent_row = (await session.execute(select(HasnAgents).where(HasnAgents.hasn_id == 'a_rl_social'))).scalar_one()
        check(
            'B social_disabled+persist',
            out.get('released') is True
            and agent_row.social_enabled is True
            and not await _suppressed_exists(session, mid),
            f'social_enabled={agent_row.social_enabled}',
        )

        # C. agent_frozen → 拒放行（released=False）+ 抑制行保留
        c = await _mk_agent(session, 'a_rl_frozen', status='disabled')
        mid = await _seed_suppressed(session, c, reason='agent_frozen')
        out = await release_suppressed(session, owner_id=OWNER, message_id=mid, mode='persist')
        check(
            'C agent_frozen 拒放行+保留',
            out.get('released') is False
            and out.get('status') == 'agent_frozen'
            and await _suppressed_exists(session, mid),
            f'status={out.get("status")} kept={await _suppressed_exists(session, mid)}',
        )

        # D. manual_only + once → 出箱、无持久副作用（不建联系人）
        d = await _mk_agent(session, 'a_rl_manual')
        # 该 agent 名下此前无 STRANGER 联系人；先删掉 A 建的（A 用的是同一 OWNER+STRANGER）
        await session.execute(
            delete(HasnContacts).where(HasnContacts.owner_id == OWNER, HasnContacts.peer_id == STRANGER)
        )
        await session.commit()
        mid = await _seed_suppressed(session, d, reason='manual_only')
        out = await release_suppressed(session, owner_id=OWNER, message_id=mid, mode='once')
        contact_after = (
            await session.execute(
                select(HasnContacts).where(HasnContacts.owner_id == OWNER, HasnContacts.peer_id == STRANGER)
            )
        ).scalar_one_or_none()
        check(
            'D manual_only+once 出箱无副作用',
            out.get('released') is True and not await _suppressed_exists(session, mid) and contact_after is None,
            f'released={out.get("released")} contact={contact_after is not None}',
        )

        # E. owner 隔离：别的 owner 放行 → not_found
        e = await _mk_agent(session, 'a_rl_iso')
        mid = await _seed_suppressed(session, e, reason='permission_denied')
        out = await release_suppressed(session, owner_id=OTHER_OWNER, message_id=mid, mode='once')
        check(
            'E owner 隔离 not_found',
            out.get('released') is False
            and out.get('status') == 'not_found'
            and await _suppressed_exists(session, mid),  # 行仍在（没被别人放行）
            f'status={out.get("status")}',
        )

        await _cleanup(session)

    print('\n=== 入站门控放行真实验证 ===')
    passed = 0
    for name, ok, detail in results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name}  →  {detail}')
        passed += ok
    print(f'\n{passed}/{len(results)} 通过')
    if passed != len(results):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
