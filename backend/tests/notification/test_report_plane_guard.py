"""NOTIF-N1 分面归属守卫真实 PG 测试（零 mock）。

事实源：`docs/hasn-node设计文档/通知系统统一设计/01-通知中心重构（分面归属·折叠进消息列表·卡片化）设计.md`
R1/R2。核心：`NotificationService.emit()` 加 OwnerLoopback 守卫——

- **自分身 → 主人（汇报面）**：source.kind==agent 且该分身的主人==recipient → 不落 hasn_notifications
  权威行，改投「主人 ⇄ 该分身」主会话一条汇报卡（content_type=5 卡片、msg_type=notification、
  primary_action 直指真实资源、无 dismiss 动作）。
- **外部用户/agent → 主人（通知面）**：照旧落 hasn_notifications 权威行。
- 守卫判据：`source.on_behalf_of==recipient`（快路）或 DB 查 HasnAgents.owner_id==recipient（兜底）。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.notification.service.notification_service import NotificationService
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _ids() -> tuple[str, str]:
    """生成一对隔离的 owner/agent hasn_id。"""
    tag = uuid.uuid4().hex[:10]
    return f'h_{tag}', f'a_{tag}'


async def _center_rows(session, target_id: str) -> list[HasnNotifications]:
    return list(
        (await session.execute(select(HasnNotifications).where(HasnNotifications.target_id == target_id)))
        .scalars()
        .all()
    )


async def _card_msgs(session, to_id: str) -> list[HasnMessages]:
    return list(
        (
            await session.execute(
                select(HasnMessages).where(
                    HasnMessages.to_id == to_id, HasnMessages.content_type == 5
                )
            )
        )
        .scalars()
        .all()
    )


# ==================== 快路：source.on_behalf_of==recipient ====================


async def test_owner_loopback_via_on_behalf_of_no_center_row(session):
    """自分身→主人（on_behalf_of 快路）：不落权威行，投主会话汇报卡。"""
    owner, agent = _ids()
    msg_id = await NotificationService.emit(
        session,
        recipient_id=owner,
        source={'kind': 'agent', 'id': agent, 'on_behalf_of': owner, 'display_name': '小星'},
        category='agent',
        type='designsystem.ready',
        title='设计系统已完成',
        body='「深空 SaaS」设计系统已就绪',
        payload={
            'link': f'/designsystem/ds_{uuid.uuid4().hex[:8]}',
            'target': {'kind': 'designsystem', 'id': 'ds_x'},
            'preview': '皇家蓝 + 深空底，8 组组件',
        },
    )
    await session.flush()

    # 未落通知中心权威行
    assert await _center_rows(session, owner) == []
    # 投了主会话汇报卡（content_type=5）
    cards = await _card_msgs(session, owner)
    assert len(cards) == 1
    card = cards[0]
    assert card.id == msg_id
    assert card.from_id == agent
    assert card.msg_type == 'notification'
    body = card.content
    assert body['schema_version'] == 'hasn.card/0.1'
    assert body['source']['kind'] == 'agent'
    assert body['metadata'].get('report') is True
    # 汇报卡是普通消息，无 dismiss/知道了 动作
    assert body['actions'] == []
    # primary_action 直指真实资源（云端权威链接被提升为 canonical hasn:// URI）
    assert body['primary_action']['uri'].startswith('hasn:/')
    assert 'designsystem' in body['primary_action']['uri']


# ==================== 兜底：DB 查 HasnAgents.owner_id==recipient ====================


async def test_owner_loopback_via_db_owner_lookup(session):
    """producer 漏带 on_behalf_of 时，守卫回退 DB 查分身主人也应判为汇报面。"""
    owner, agent = _ids()
    session.add(HasnAgents(hasn_id=agent, owner_id=owner, display_name='小星', agent_name='xiaoxing'))
    await session.flush()

    await NotificationService.emit(
        session,
        recipient_id=owner,
        source={'kind': 'agent', 'id': agent},  # 故意不带 on_behalf_of
        category='reminder',
        type='task.pending_approval',
        title='任务待审批',
        payload={'link': f'/tasks/sessions/{uuid.uuid4().hex[:8]}', 'target': {'kind': 'task', 'id': 't1'}},
    )
    await session.flush()

    assert await _center_rows(session, owner) == []
    assert len(await _card_msgs(session, owner)) == 1


# ==================== 通知面：外部事件照旧落权威行 ====================


async def test_external_event_still_creates_center_row(session):
    """外部用户点赞→主人：仍落 hasn_notifications 权威行（通知面）。"""
    owner, _ = _ids()
    actor, _ = _ids()  # 另一个人（外部触发者）
    await NotificationService.emit(
        session,
        recipient_id=owner,
        source={'kind': 'user', 'id': actor, 'display_name': '路人甲'},
        category='social',
        type='post_liked',
        title='路人甲 赞了你的帖子',
        payload={'link': f'/community/posts/{uuid.uuid4().hex[:8]}'},
    )
    await session.flush()

    rows = await _center_rows(session, owner)
    assert len(rows) == 1
    assert rows[0].category == 'social'
    assert rows[0].type == 'post_liked'


async def test_agent_to_other_owner_not_loopback(session):
    """A 主人的分身→B 主人：非 OwnerLoopback（on_behalf_of=A ≠ recipient=B），仍走通知面。"""
    owner_b, _ = _ids()
    owner_a, agent_a = _ids()
    await NotificationService.emit(
        session,
        recipient_id=owner_b,
        source={'kind': 'agent', 'id': agent_a, 'on_behalf_of': owner_a, 'display_name': 'A的分身'},
        category='social',
        type='contact_request',
        title='A 的分身请求添加你',
        payload={'link': '/contacts/requests'},
    )
    await session.flush()

    # 收件人是 B，落 B 的通知中心权威行
    rows = await _center_rows(session, owner_b)
    assert len(rows) == 1


# ==================== 读侧纵深防御：list/count 排除漏进的汇报面行 ====================
#
# 写侧 emit() 的 OwnerLoopback 守卫是 2026-07-07 才上线；此前「自分身→主人」的完成通知
# 已经落进了 hasn_notifications 权威行（福仔截图里的「设计系统已完成」卡就是这类历史遗留）。
# 直接插一条这样的行（绕过 emit 守卫）模拟遗留 / 旁路 producer，验证读侧 list/count 把它剔除。


def _leaked_report_row(target_id: str, agent_id: str, *, on_behalf_of: str | None) -> HasnNotifications:
    """构造一条「本该走汇报面却漏进通知中心」的未读权威行（绕过 emit 守卫直插 DB）。"""
    source: dict[str, str] = {'kind': 'agent', 'id': agent_id, 'display_name': '小星'}
    if on_behalf_of is not None:
        source['on_behalf_of'] = on_behalf_of
    return HasnNotifications(
        target_id=target_id,
        type='designsystem.ready',
        title='设计系统「唤星 Astra 投资路演产品版设计规范」已完成',
        body='评分 100',
        category='system',
        source=source,
        read=False,
        state='unread',
    )


def _external_row(target_id: str, actor_id: str) -> HasnNotifications:
    """构造一条正常的外部未读通知行（应被读侧保留）。"""
    return HasnNotifications(
        target_id=target_id,
        type='post_liked',
        title='路人甲 赞了你的帖子',
        category='social',
        source={'kind': 'user', 'id': actor_id, 'display_name': '路人甲'},
        read=False,
        state='unread',
    )


async def test_read_side_excludes_leaked_loopback_via_on_behalf_of(session):
    """漏进的自分身汇报行（on_behalf_of==主人）：list 不返、unread_count 不计；外部行照常。"""
    owner, agent = _ids()
    actor, _ = _ids()
    session.add(_leaked_report_row(owner, agent, on_behalf_of=owner))
    session.add(_external_row(owner, actor))
    await session.flush()

    listed = await NotificationService.list_notifications(session, recipient_hasn_id=owner, limit=50)
    types = {it['type'] for it in listed['items']}
    assert types == {'post_liked'}  # 汇报行被剔除，只剩外部行

    counts = await NotificationService.unread_count(session, recipient_hasn_id=owner)
    assert counts['total'] == 1
    assert counts['by_type'] == {'post_liked': 1}


async def test_read_side_excludes_leaked_loopback_via_db_owner(session):
    """漏进的自分身汇报行（无 on_behalf_of，靠 owned_agent_ids 子查询命中）：一样被剔除。"""
    owner, agent = _ids()
    session.add(HasnAgents(hasn_id=agent, owner_id=owner, display_name='小星', agent_name='xiaoxing'))
    session.add(_leaked_report_row(owner, agent, on_behalf_of=None))  # 故意不带 on_behalf_of
    await session.flush()

    listed = await NotificationService.list_notifications(session, recipient_hasn_id=owner, limit=50)
    assert listed['items'] == []
    counts = await NotificationService.unread_count(session, recipient_hasn_id=owner)
    assert counts['total'] == 0


async def test_read_side_keeps_other_owner_agent_row(session):
    """他人分身发来的通知（agent→他人，owner≠主人）不是汇报面，读侧必须保留。"""
    owner_b, _ = _ids()
    owner_a, agent_a = _ids()
    # A 的分身给 B 发的通知漏成 B 通知中心一行：on_behalf_of=A≠B，且 B 不拥有 agent_a
    session.add(_leaked_report_row(owner_b, agent_a, on_behalf_of=owner_a))
    await session.flush()

    listed = await NotificationService.list_notifications(session, recipient_hasn_id=owner_b, limit=50)
    assert len(listed['items']) == 1
    counts = await NotificationService.unread_count(session, recipient_hasn_id=owner_b)
    assert counts['total'] == 1


async def test_read_side_keeps_external_empty_source_row(session):
    """空/无 source 的历史行（JSONB `{}` → kind 为 NULL）不得被 NULL 逻辑误杀。"""
    owner, _ = _ids()
    session.add(
        HasnNotifications(
            target_id=owner,
            type='system',
            title='系统公告',
            category='system',
            source={},  # 空 source：coalesce 兜底后应保留
            read=False,
            state='unread',
        )
    )
    await session.flush()

    listed = await NotificationService.list_notifications(session, recipient_hasn_id=owner, limit=50)
    assert len(listed['items']) == 1
    counts = await NotificationService.unread_count(session, recipient_hasn_id=owner)
    assert counts['total'] == 1


# ==================== 判据纯逻辑单测（on_behalf_of 快路，db 不被触碰）====================


async def test_is_owner_loopback_discriminator():
    owner, agent = _ids()
    # agent 且 on_behalf_of==recipient → True
    assert (
        await NotificationService._is_owner_loopback(
            None, source={'kind': 'agent', 'id': agent, 'on_behalf_of': owner}, recipient_id=owner
        )
        is True
    )
    # 外部 user → False
    assert (
        await NotificationService._is_owner_loopback(
            None, source={'kind': 'user', 'id': 'h_other'}, recipient_id=owner
        )
        is False
    )
    # agent 但 on_behalf_of 指向别人 → False
    assert (
        await NotificationService._is_owner_loopback(
            None, source={'kind': 'agent', 'id': agent, 'on_behalf_of': 'h_someone'}, recipient_id=owner
        )
        is False
    )
