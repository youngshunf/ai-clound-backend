"""入站门控（外部→Agent）五闸真实验证（零 mock，连真实本地 PG）。

跑法：uv run python -m backend.scripts.verify_inbound_gate
覆盖 evaluate_inbound 七场景 + record_suppression 落库。临时数据用 _gv_ 前缀，跑前后自清理。
"""

import asyncio
import uuid

from sqlalchemy import delete, select

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_suppressed_messages import HasnSuppressedMessages
from backend.app.hasn.service.inbound_gatekeeper import (
    ALLOW,
    REJECT_SILENT,
    SUPPRESS,
    evaluate_inbound,
    record_suppression,
)
from backend.database.db import async_db_session

OWNER = 'h_gv_owner'
STRANGER = 'h_gv_stranger'
NORMAL = 'h_gv_normal'
FRIEND = 'h_gv_friend'
BLOCKED = 'h_gv_blocked'


async def _cleanup(session) -> None:
    await session.execute(delete(HasnSuppressedMessages).where(HasnSuppressedMessages.owner_id == OWNER))
    await session.execute(delete(HasnContacts).where(HasnContacts.owner_id == OWNER))
    await session.execute(delete(HasnAgents).where(HasnAgents.owner_id == OWNER))
    await session.commit()


async def _mk_agent(session, hasn_id, *, status='active', social=True, policy='auto'):
    session.add(
        HasnAgents(
            hasn_id=hasn_id,
            star_id=hasn_id.replace('a_gv_', 'gv') + '#star',
            owner_id=OWNER,
            display_name='门控测试分身',
            status=status,
            social_enabled=social,
            inbound_policy=policy,
        )
    )
    await session.commit()
    return {'hasn_id': hasn_id, 'owner_id': OWNER}


async def _mk_contact(session, peer_id, *, trust, status='connected', relation='social') -> None:
    session.add(
        HasnContacts(
            owner_id=OWNER,
            peer_id=peer_id,
            peer_type='human',
            relation_type=relation,
            trust_level=trust,
            status=status,
        )
    )
    await session.commit()


async def main() -> None:
    results = []
    async with async_db_session() as session:
        await _cleanup(session)
        # evaluate_inbound 只读 agents/contacts；from_id 是字符串无需 seed humans
        await _mk_contact(session, NORMAL, trust=2)  # 普通联系人 → social[2] send_message ALLOW
        await _mk_contact(session, FRIEND, trust=3)  # 朋友 → social[3] ALLOW
        await _mk_contact(session, BLOCKED, trust=0, status='blocked')  # 黑名单

        def check(name, outcome, want_action, want_reason=None) -> None:
            ok = outcome.action == want_action and (want_reason is None or outcome.reason == want_reason)
            results.append((name, ok, f'{outcome.action}/{outcome.reason}'))

        # A. agent_frozen（disabled）——陌生人发
        a = await _mk_agent(session, 'a_gv_frozen', status='disabled')
        check('A agent_frozen', await evaluate_inbound(session, from_id=STRANGER, agent_info=a), SUPPRESS, 'agent_frozen')

        # B. social_disabled
        a = await _mk_agent(session, 'a_gv_nosocial', social=False)
        check('B social_disabled', await evaluate_inbound(session, from_id=STRANGER, agent_info=a), SUPPRESS, 'social_disabled')

        # C. permission_denied（陌生人，无 contact，matrix social[1] DENY）
        a = await _mk_agent(session, 'a_gv_open')
        check('C permission_denied(stranger)', await evaluate_inbound(session, from_id=STRANGER, agent_info=a), SUPPRESS, 'permission_denied')

        # D. allow（普通联系人 trust=2）
        check('D allow(normal)', await evaluate_inbound(session, from_id=NORMAL, agent_info=a), ALLOW)

        # E. reject_silent（黑名单）
        check('E reject_silent(blocked)', await evaluate_inbound(session, from_id=BLOCKED, agent_info=a), REJECT_SILENT)

        # F. owner 直放
        check('F owner_passthrough', await evaluate_inbound(session, from_id=OWNER, agent_info=a), ALLOW)

        # G. manual_only（manual_all 策略，朋友 trust=3 矩阵本允许，仍强制暂留）
        a2 = await _mk_agent(session, 'a_gv_manual', policy='manual_all')
        check('G manual_only(manual_all)', await evaluate_inbound(session, from_id=FRIEND, agent_info=a2), SUPPRESS, 'manual_only')

        # H. record_suppression 落库 + 查回
        conv = str(uuid.uuid4())
        msg_id = 9_000_000_777
        await record_suppression(
            session, message_id=msg_id, owner_id=OWNER, hasn_id='a_gv_open',
            conversation_id=conv, reason='permission_denied', policy_snapshot={'trust_level': 1},
        )
        await session.commit()
        row = (
            await session.execute(
                select(HasnSuppressedMessages).where(HasnSuppressedMessages.message_id == msg_id)
            )
        ).scalar_one_or_none()
        results.append((
            'H record_suppression 落库',
            row is not None and row.suppress_reason == 'permission_denied' and row.visible_to_owner is True,
            f'reason={getattr(row, "suppress_reason", None)} visible={getattr(row, "visible_to_owner", None)}',
        ))

        await _cleanup(session)

    print('\n=== 入站门控五闸真实验证 ===')
    passed = 0
    for name, ok, got in results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name}  →  {got}')
        passed += ok
    print(f'\n{passed}/{len(results)} 通过')
    if passed != len(results):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
