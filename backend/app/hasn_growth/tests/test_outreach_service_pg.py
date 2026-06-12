"""获客触达状态机 + 合规 M3-b 真实 PG 验收（零 mock，事务末尾回滚）。

覆盖 §8.2 G4 状态机（首触达必审/审批/拒绝/edit-then-approve/发送态流转/回复入站）与
§10.3 合规闸门（极限词/频控/optout 硬闸）+ 跨户隔离。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.hasn_growth.model.lead_audit_log import LeadAuditLog
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.service.dispatch_service import (
    get_channel_setting,
    growth_dispatch_service,
    set_wechat_auto_send,
)
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


async def test_send_material_returns_plaintext_and_audits(session) -> None:
    """M6 manual_assist：approved 触达 → 复制发送素材包回明文联系方式 + 落 pii_read 审计。"""
    uid = 992000 + int(uuid.uuid4().int % 9000)
    other_uid = uid + 1
    cid = await _qualified_customer(session, user_id=uid, email='buy@eta.com', company='Eta')

    m = await growth_outreach_service.send_outreach(
        session, user_id=uid, customer_id=cid, channel='manual_assist', content='您好，约个时间聊聊', agent_id='a_z1'
    )
    # 仅 approved 可取素材包；pending 取 → Forbidden
    with pytest.raises(errors.ForbiddenError):
        await growth_outreach_service.build_send_material(
            session, user_id=uid, message_id=m['id'], actor_user_id=uid
        )
    await growth_outreach_service.approve_outreach(session, user_id=uid, message_id=m['id'], approver_user_id=uid)

    packet = await growth_outreach_service.build_send_material(
        session, user_id=uid, message_id=m['id'], actor_user_id=uid
    )
    # 明文联系方式仅此处渲染
    assert packet['contact']['phone'] == '13800138000'
    assert packet['contact']['email'] == 'buy@eta.com'
    assert packet['content'] == '您好，约个时间聊聊'
    assert packet['channel'] == 'manual_assist'

    # 落了一条 pii_read 审计，且 payload 不含明文 PII（assert_audit_payload_safe 已在 service 内守卫）
    audits = (
        await session.execute(
            select(LeadAuditLog).where(
                LeadAuditLog.event_type == 'pii_read', LeadAuditLog.target_ref == str(m['id'])
            )
        )
    ).scalars().all()
    assert audits and audits[0].actor_user_id == uid
    assert '13800138000' not in str(audits[0].payload) and 'buy@eta.com' not in str(audits[0].payload)

    # 跨户：他人取本户素材包 → NotFound
    with pytest.raises(errors.NotFoundError):
        await growth_outreach_service.build_send_material(
            session, user_id=other_uid, message_id=m['id'], actor_user_id=other_uid
        )


async def _owner_with_task(sess, *, uid: int, state: str) -> tuple[str, str]:
    """建主人身份 + 一条指定 state 的 hasn_task 跟进任务，返回 (owner_hasn_id, task_uuid)。"""
    tag = uuid.uuid4().hex[:8]
    owner_hasn = f'h_j3_{tag}'
    task_uuid = f'tk_j3_{tag}'
    sess.add(HasnHumans(hasn_id=owner_hasn, star_id=f's_{uid}', user_id=uid, nickname='主人', status='active'))
    await sess.flush()
    await sess.execute(
        text(
            'INSERT INTO hasn_task.task (owner_id, agent_id, name, prompt, schedule_type, schedule_config, state, task_uuid) '
            "VALUES (:o, :a, :n, :p, 'interval', '{}'::jsonb, :st, :u)"
        ),
        {'o': owner_hasn, 'a': 'a_j3', 'n': '跟进任务', 'p': '跟进客户', 'st': state, 'u': task_uuid},
    )
    await sess.flush()
    return owner_hasn, task_uuid


async def test_j3_inbound_triggers_run_now_and_debounces(session) -> None:
    """M6 J3：客户回复 → 绑定跟进任务（scheduled）则触发 run_now；10 分钟窗口内第二条回复防抖合并。"""
    uid = 994000 + int(uuid.uuid4().int % 5000)
    owner_hasn, task_uuid = await _owner_with_task(session, uid=uid, state='scheduled')
    cid = await _qualified_customer(session, user_id=uid, email=f'j3a{uuid.uuid4().hex[:6]}@eta.com', company='EtaJ3')
    # 分身绑定跟进任务（复用客户 PATCH followup_task_id 路径）
    await growth_funnel_service.update_customer_profile(
        session, user_id=uid, customer_id=cid, followup_task_id=task_uuid
    )

    # 第一条回复 → run_now 触发
    r1 = await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='可以聊聊'
    )
    assert r1['followup_trigger'] == {'triggered': True, 'reason': 'run_now'}
    # run_now 真生效：任务 next_run_at 被置（云端置位，节点本地 tick 拾取）
    nra = (
        await session.execute(
            text('SELECT next_run_at FROM hasn_task.task WHERE owner_id = :o AND task_uuid = :u'),
            {'o': owner_hasn, 'u': task_uuid},
        )
    ).scalar_one()
    assert nra is not None

    # 第二条回复（同窗口）→ 防抖合并，不再触发
    r2 = await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='你那边方便吗'
    )
    assert r2['followup_trigger'] == {'triggered': False, 'reason': 'debounced'}


async def test_j3_no_followup_task_is_noop(session) -> None:
    """M6 J3：客户未绑定跟进任务 → 不触发（兜底：任务 interval 节奏照常，不丢跟进），且不报错。"""
    uid = 995000 + int(uuid.uuid4().int % 5000)
    session.add(
        HasnHumans(hasn_id=f'h_j3n_{uuid.uuid4().hex[:8]}', star_id=f's_{uid}', user_id=uid, nickname='主人', status='active')
    )
    await session.flush()
    cid = await _qualified_customer(session, user_id=uid, email=f'j3n{uuid.uuid4().hex[:6]}@eta.com', company='EtaJ3N')

    r = await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='我再看看'
    )
    assert r['followup_trigger'] == {'triggered': False, 'reason': 'no_followup_task'}


async def test_j3_unrunnable_task_degrades(session) -> None:
    """M6 J3：跟进任务非 scheduled/paused（如待审批）→ 如实不触发、不报错、不占防抖。"""
    uid = 996000 + int(uuid.uuid4().int % 5000)
    _owner, task_uuid = await _owner_with_task(session, uid=uid, state='pending_approval')
    cid = await _qualified_customer(session, user_id=uid, email=f'j3r{uuid.uuid4().hex[:6]}@eta.com', company='EtaJ3R')
    await growth_funnel_service.update_customer_profile(
        session, user_id=uid, customer_id=cid, followup_task_id=task_uuid
    )

    r = await growth_outreach_service.record_inbound_reply(
        session, user_id=uid, customer_id=cid, channel='wechat', content='在的'
    )
    assert r['followup_trigger'] == {'triggered': False, 'reason': 'task_not_runnable'}
    # 不占防抖：客户 last_followup_trigger_at 仍为空，任务空闲后下条回复可即时触发
    trig_at = (
        await session.execute(
            text('SELECT last_followup_trigger_at FROM hasn_growth.customer WHERE id = :c'), {'c': cid}
        )
    ).scalar_one()
    assert trig_at is None


async def _approved_outreach(sess, *, uid: int, cid: int, channel: str, content: str = '您好，跟进一下我们的方案') -> int:
    """建一条 approved 出站触达（首触达必审 → 主人批准），返回 message_id。"""
    m = await growth_outreach_service.send_outreach(
        sess, user_id=uid, customer_id=cid, channel=channel, content=content, agent_id='a_disp'
    )
    assert m['status'] == 'pending_approval'
    a = await growth_outreach_service.approve_outreach(sess, user_id=uid, message_id=m['id'], approver_user_id=uid)
    assert a['status'] == 'approved'
    return a['id']


async def _msg_status(sess, mid: int) -> str:
    return (
        await sess.execute(text('SELECT status FROM hasn_growth.outreach_message WHERE id = :m'), {'m': mid})
    ).scalar_one()


async def test_dispatch_quiet_hours_queues(session) -> None:
    """M6 worker：静默时段（窗口外）→ 挂起，触达保持 approved 不发。"""
    uid = 997000 + int(uuid.uuid4().int % 4000)
    cid = await _qualified_customer(session, user_id=uid, email=f'dq{uuid.uuid4().hex[:6]}@eta.com', company='EtaDQ')
    mid = await _approved_outreach(session, uid=uid, cid=cid, channel='email')

    stat = await growth_dispatch_service.dispatch_approved_batch(session, limit=200, now_hour=3)
    assert stat['queued_quiet_hours'] >= 1
    assert await _msg_status(session, mid) == 'approved'  # 未发，保持待发


async def test_dispatch_skips_manual_assist(session) -> None:
    """M6 worker：manual_assist 渠道 owner 手动发，worker 不接管 → 跳过保持 approved。"""
    uid = 998000 + int(uuid.uuid4().int % 4000)
    cid = await _qualified_customer(session, user_id=uid, email=f'dm{uuid.uuid4().hex[:6]}@eta.com', company='EtaDM')
    mid = await _approved_outreach(session, uid=uid, cid=cid, channel='manual_assist')

    stat = await growth_dispatch_service.dispatch_approved_batch(session, limit=200, now_hour=10)
    assert stat['skipped_manual'] >= 1
    assert await _msg_status(session, mid) == 'approved'


async def test_dispatch_channel_unavailable_marks_failed(session) -> None:
    """M6 worker：无可用 transport → channel_unavailable → mark_failed + 建议回退 manual_assist（零 fake，不假装已发）。"""
    uid = 999000 + int(uuid.uuid4().int % 900)
    cid = await _qualified_customer(session, user_id=uid, email=f'du{uuid.uuid4().hex[:6]}@eta.com', company='EtaDU')
    mid = await _approved_outreach(session, uid=uid, cid=cid, channel='email')

    stat = await growth_dispatch_service.dispatch_approved_batch(session, limit=200, now_hour=10)
    assert stat['failed'] >= 1
    assert await _msg_status(session, mid) == 'failed'
    err = (
        await session.execute(text('SELECT error_message FROM hasn_growth.outreach_message WHERE id = :m'), {'m': mid})
    ).scalar_one()
    assert 'channel_unavailable' in (err or '') and 'manual_assist' in (err or '')


async def test_dispatch_wechat_j1_gate(session) -> None:
    """M6 worker：微信 J1 闸门——未确认 → 跳过保持 approved（恒 manual）；确认后才进 worker。"""
    uid = 991000 + int(uuid.uuid4().int % 4000)
    cid = await _qualified_customer(session, user_id=uid, email=f'dw{uuid.uuid4().hex[:6]}@eta.com', company='EtaDW')
    mid = await _approved_outreach(session, uid=uid, cid=cid, channel='wechat')

    # 未确认 → 跳过，保持 approved
    stat1 = await growth_dispatch_service.dispatch_approved_batch(session, limit=200, now_hour=10)
    assert stat1['wechat_unconfirmed'] >= 1
    assert await _msg_status(session, mid) == 'approved'

    # owner 确认微信自动发送（M7 UI 二次确认开关）后才进 worker（无 wechat transport → 如实 failed）
    await set_wechat_auto_send(session, user_id=uid, confirmed=True)
    stat2 = await growth_dispatch_service.dispatch_approved_batch(session, limit=200, now_hour=10)
    assert stat2['wechat_unconfirmed'] == 0
    assert await _msg_status(session, mid) == 'failed'


async def test_channel_setting_roundtrip(session) -> None:
    """M7 渠道设置：缺省安全关 → 开 → 关，幂等可重复（owner GET/PUT 端点的 service 路径）。"""
    uid = 990500 + int(uuid.uuid4().int % 400)
    # 缺省（无设置行）= 安全默认关
    assert (await get_channel_setting(session, user_id=uid)) == {'wechat_auto_send_confirmed': False}
    # 开（UI 二次确认后写入）
    await set_wechat_auto_send(session, user_id=uid, confirmed=True)
    assert (await get_channel_setting(session, user_id=uid)) == {'wechat_auto_send_confirmed': True}
    # 关（随时可关）+ upsert 幂等
    await set_wechat_auto_send(session, user_id=uid, confirmed=False)
    await set_wechat_auto_send(session, user_id=uid, confirmed=False)
    assert (await get_channel_setting(session, user_id=uid)) == {'wechat_auto_send_confirmed': False}
