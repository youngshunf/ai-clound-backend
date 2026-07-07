"""NOTIF-N3 通知行 → 卡片投影真实测试（零 mock）。

doc `通知系统统一设计/01` §3.4：cloud 在 `GET /notifications` 序列化时把权威通知行投影成
`hasn.card/0.1` 卡片体（前端零拼装，折叠进消息列表后直接 CardMessage 渲染）。

覆盖：
- 社交通知（user 源，link=/community/posts/{id}）→ 投影出合法卡片，primary_action 深链指向
  **目标资源** `hasn://community/posts/{id}`（非通知自身），source.kind=user；
- 联系人通知（agent 源，被关注 relay）→ 投影出 source.kind=agent 卡片；
- link 为非法 scheme（如 javascript:）→ 投影降级（primary_action 省略但卡片仍合法或整卡 None），
  绝不抛异常拖垮整列（URI 白名单韧性）；
- 来源 kind 非 CardSourceKind（如缺 kind）→ 返回 None（前端回退扁平字段）；
- 端到端：emit 一条社交通知落真实 PG → list_notifications 返回项带合法 `card`。

真实本地 PostgreSQL（端口 15432）；不可达则 skip。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.notification.service.notification_carrier import project_notification_card
from backend.app.notification.service.notification_service import notification_service
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


def _row(*, source: dict | None, title: str, data: dict, category: str = 'social') -> SimpleNamespace:
    """构造一条内存态权威通知行占位（投影为纯读，只读属性，不落库、不建 ORM 实例）。

    `project_notification_card` / `build_card_body` 只读 source/data/id/title/body/category/type/priority，
    用 SimpleNamespace 即可，避开 ORM `__init__`（id 为 init=False 的主键列，不接受 kwarg）。
    """
    return SimpleNamespace(
        id=12345,
        target_id='h_owner_x',
        type='community_like',
        category=category,
        priority='normal',
        source=source,
        title=title,
        body=None,
        data=data,
        read=False,
        state='unread',
    )


# ==================== 纯投影（无 DB） ====================


def test_project_social_card_points_to_target_resource() -> None:
    """社交通知投影：primary_action 深链指向目标资源（帖子），非通知自身；source.kind=user。"""
    notif = _row(
        source={'kind': 'user', 'id': 'h_actor_1', 'display_name': '张三', 'avatar': 'https://x/a.png'},
        title='张三赞了你的帖子',
        data={'preview': '一段内容', 'link': '/community/posts/post_cloud_001',
              'target': {'type': 'post', 'id': 'post_cloud_001'}},
    )
    card = project_notification_card(notif)
    assert card is not None
    # 合法 hasn.card/0.1（再校验一次，确保契约有效）
    validate_card_message_body(card)
    assert card['source']['kind'] == 'user'
    assert card['source']['display_name'] == '张三'
    # 深链指向目标资源（相对 /community/posts/{id} 提升为 hasn://community/posts/{id}），非 notification 自身
    assert card['primary_action']['uri'] == 'hasn://community/posts/post_cloud_001'
    assert card['primary_action']['uri'] != 'hasn://notification/12345'
    # resource 体标识仍是 notification（不做打开入口）
    assert card['resource']['uri'] == 'hasn://notification/12345'


def test_project_agent_source_card() -> None:
    """联系人面 agent 源（别人的分身关注了你）→ 投影 source.kind=agent 卡片。"""
    notif = _row(
        source={'kind': 'agent', 'id': 'a_peer_9', 'display_name': '明远', 'avatar': ''},
        title='明远关注了你',
        data={'link': '/community/profiles/a_peer_9', 'target': {'type': 'agent', 'id': 'a_peer_9'}},
        category='contact',
    )
    card = project_notification_card(notif)
    assert card is not None
    validate_card_message_body(card)
    assert card['source']['kind'] == 'agent'
    assert card['primary_action']['uri'] == 'hasn://community/profiles/a_peer_9'


def test_project_bad_scheme_link_degrades_gracefully() -> None:
    """link 为非法 scheme（javascript:）→ 不抛异常；卡片要么无 primary_action、要么整卡 None。"""
    notif = _row(
        source={'kind': 'user', 'id': 'h_actor_1', 'display_name': '张三', 'avatar': ''},
        title='张三赞了你的帖子',
        data={'link': 'javascript:alert(1)', 'target': {'type': 'post', 'id': 'p1'}},
    )
    # 关键：绝不抛异常
    card = project_notification_card(notif)
    if card is not None:
        validate_card_message_body(card)
        # 非法 scheme 不得成为可点深链
        pa = card.get('primary_action')
        assert pa is None or pa['uri'].startswith(('hasn:', 'http:', 'https:'))


def test_project_missing_kind_returns_none() -> None:
    """来源 kind 缺失/非 CardSourceKind → None（前端回退扁平字段渲染）。"""
    assert project_notification_card(_row(source=None, title='x', data={})) is None
    assert project_notification_card(_row(source={'id': 'x'}, title='x', data={})) is None
    assert project_notification_card(_row(source={'kind': 'weird'}, title='x', data={})) is None


# ==================== 端到端（真实 PG，list_notifications 带 card） ====================


async def test_list_notifications_includes_card(session) -> None:
    """emit 一条社交通知落真实 PG → list_notifications 返回项带合法 card 投影。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    await notification_service.emit(
        session,
        recipient_id=owner,
        source={'kind': 'user', 'id': f'h_actor_{tag}', 'display_name': '点赞者', 'avatar': ''},
        category='social',
        type='community_like',
        title='点赞者赞了你的帖子',
        payload={'preview': '正文', 'link': f'/community/posts/post_{tag}',
                 'target': {'type': 'post', 'id': f'post_{tag}'}},
    )
    result = await notification_service.list_notifications(session, recipient_hasn_id=owner)
    items = result['items']
    assert len(items) >= 1
    top = items[0]
    assert 'card' in top
    card = top['card']
    assert card is not None
    validate_card_message_body(card)
    assert card['source']['kind'] == 'user'
    assert card['primary_action']['uri'] == f'hasn://community/posts/post_{tag}'
