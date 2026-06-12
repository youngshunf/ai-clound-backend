"""获客触达状态机 + 合规 M3-b 真实 PG 验收（零 mock，事务末尾回滚）。

覆盖 §8.2 G4 状态机（首触达必审/审批/拒绝/edit-then-approve/发送态流转/回复入站）与
§10.3 合规闸门（极限词/频控/optout 硬闸）+ 跨户隔离。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.service.funnel_service import growth_funnel_service
from backend.app.hasn_growth.service.outreach_service import growth_outreach_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _qualified_customer(sess, *, user_id: int, email: str, company: str) -> int:
    lead = LeadContact(
        lead_no=f'L{uuid.uuid4().hex[:10].upper()}',
        lead_scope='user',
        user_id=user_id,
        company_name=company,
        contact_name='李四',
        email=email,
        phone='13800138000',
        source_type='firecrawl',
        status='valid',
        confidence_score=70,
    )
    sess.add(lead)
    await sess.flush()
    cust = await growth_funnel_service.qualify_lead(sess, user_id=user_id, lead_contact_id=lead.id)
    return cust['id']


async def test_outreach_state_machine(session) -> None:
    uid = 980000 + int(uuid.uuid4().int % 9000)
    other_uid = uid + 1
    cid = await _qualified_customer(session, user_id=uid, email='lisi@beta.com', company='Beta')

    # T1 首触达 → 必 pending_approval（不可豁免），即便传 whitelist 也必审
    m1 = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=cid, channel='manual_assist',
        content='您好，想介绍我们的获客方案', agent_id='a_y1', intent_note='首次破冰',
        whitelist_auto_send=True,
    )
    assert m1['status'] == 'pending_approval' and m1['auto_approved'] is False
    assert m1['direction'] == 'outbound'

    # 待审队列含这条
    pending = await growth_outreach_service.list_pending_approvals(session, user_id=uid)
    assert any(p['id'] == m1['id'] for p in pending)

    # T2 edit-then-approve → approved + 改话术留痕
    m1a = await growth_outreach_service.approve_outreach(
        session, user_id=uid, message_id=m1['id'], approver_user_id=uid, edited_content='您好，想约个时间聊聊获客'
    )
    assert m1a['status'] == 'approved'
    assert m1a['content'] == '您好，想约个时间聊聊获客'
    assert m1a['content_assets'].get('revisions') and len(m1a['content_assets']['revisions']) == 1

    # T3 发送态流转 approved → sending → sent
    await growth_outreach_service.mark_sending(session, user_id=uid, message_id=m1['id'])
    m1s = await growth_outreach_service.mark_sent(session, user_id=uid, message_id=m1['id'], channel_actual='wechat')
    assert m1s['status'] == 'sent' and m1s['sent_at'] is not None
    assert m1s['content_assets'].get('channel_actual') == 'wechat'

    # 拒绝路径需另起一条 pending（T1 已 sent）。再发一条 email 首触达
    m2 = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=cid, channel='email', content='跟进一下', agent_id='a_y1'
    )
    assert m2['status'] == 'pending_approval'
    m2r = await growth_outreach_service.reject_outreach(
        session, user_id=uid, message_id=m2['id'], approver_user_id=uid, reason='太频繁，缓一缓'
    )
    assert m2r['status'] == 'rejected' and m2r['reject_reason'] == '太频繁，缓一缓'
    # 拒因回流 timeline
    timeline = await growth_funnel_service.customer_timeline(session, user_id=uid, customer_id=cid)
    assert any(t['kind'] == 'note' and '太频繁' in (t['content'] or '') for t in timeline)

    # T4 客户回复（inbound）→ 客户态 engaged + 清零静默
    await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='可以，周三下午方便'
    )
    cust = await growth_funnel_service.get_customer(session, user_id=uid, customer_id=cid)
    assert cust['lifecycle_status'] == 'engaged' and cust['silent_round_count'] == 0

    # T5 非首触达（manual_assist 渠道 T1 已有出站）+ 白名单 + 客户已回复 → 自动放行 approved(auto_approved=true)
    m3 = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=cid, channel='manual_assist', content='周三 15:00 给您电话',
        agent_id='a_y1', whitelist_auto_send=True,
    )
    assert m3['status'] == 'approved' and m3['auto_approved'] is True

    # 跨户隔离：他人发本户客户 → NotFound；他人批本户消息 → NotFound
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.send_outreach(
            session, user_id=other_uid, customer_id=cid, channel='manual_assist', content='x'
        )
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.approve_outreach(
            session, user_id=other_uid, message_id=m3['id'], approver_user_id=other_uid
        )


async def test_outreach_compliance_gates(session) -> None:
    uid = 985000 + int(uuid.uuid4().int % 9000)

    # 极限词 → blocked_compliance（且不计入频控配额）
    cb = await _qualified_customer(session, user_id=uid, email='ad@gamma.com', company='Gamma')
    blocked = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=cb, channel='email', content='我们是全网第一，效果100%保证'
    )
    assert blocked['status'] == 'blocked_compliance'
    assert blocked['compliance_check']['banned_words']

    # 频控：同客户同渠道一周内 ≤2，第三条被拦
    cf = await _qualified_customer(session, user_id=uid, email='freq@delta.com', company='Delta')
    f1 = await growth_outreach_service.send_outreach(session, user_id=uid, customer_id=cf, channel='email', content='第一封跟进')
    f2 = await growth_outreach_service.send_outreach(session, user_id=uid, customer_id=cf, channel='email', content='第二封跟进')
    f3 = await growth_outreach_service.send_outreach(session, user_id=uid, customer_id=cf, channel='email', content='第三封跟进')
    assert f1['status'] == 'pending_approval' and f2['status'] == 'pending_approval'
    assert f3['status'] == 'blocked_compliance' and f3['compliance_check']['freq_exceeded'] is True

    # optout 硬闸：登记客户邮箱退订（all）后，任意渠道触达被拦
    co = await _qualified_customer(session, user_id=uid, email='stop@epsilon.com', company='Epsilon')
    res = await growth_outreach_service.register_optout(
        session, user_id=uid, channel='all', address='stop@epsilon.com', customer_id=co, reason='客户明示拒绝'
    )
    assert res['created'] is True
    # 幂等：再登记不重复
    res2 = await growth_outreach_service.register_optout(session, user_id=uid, channel='all', address='stop@epsilon.com')
    assert res2['created'] is False
    blocked_opt = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=co, channel='wechat', content='在吗'
    )
    assert blocked_opt['status'] == 'blocked_optout'


async def test_inbound_reply_emits_owner_notification(session) -> None:
    """M6：客户回复（inbound 落库）→ 给主人发一条 growth.reply.received 通知卡片。

    需主人有 HasnHumans 行（user_id↔hasn_id）才解析得到 recipient；无身份则诚实不发（不造假）。
    """
    tag = uuid.uuid4().hex[:8]
    uid = 990000 + int(uuid.uuid4().int % 9000)
    owner_hasn = f'h_grw_{tag}'
    session.add(
        HasnHumans(hasn_id=owner_hasn, star_id=f's_{uid}', user_id=uid, nickname='主人', status='active')
    )
    await session.flush()
    cid = await _qualified_customer(session, user_id=uid, email=f'reply{tag}@zeta.com', company='Zeta')

    await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='可以聊聊，周三下午方便'
    )

    notif = (
        await session.execute(
            select(HasnNotifications).where(
                HasnNotifications.type == 'growth.reply.received',
                HasnNotifications.target_id == owner_hasn,
            )
        )
    ).scalars().all()
    assert notif, '客户回复应给主人落一条 growth.reply.received 通知'
    assert any('回复' in (n.title or '') for n in notif)
