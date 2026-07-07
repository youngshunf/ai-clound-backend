"""NOTIFUX-3 通知面 → WSPUSH KIND_NOTIFICATION bump 真实 PG 测试（零 mock）。

事实源：福仔「收到通知消息也要有系统通知」。核心：`NotificationService.emit()` 落**通知面**权威
行后 bump 该 owner 的通知 revision（WSPUSH `KIND_NOTIFICATION`）→ 在线节点 daemon 收到即拉未读
通知、diff 出新增未读项发原生系统通知。断言两件事：

1. **通知面**（外部用户/agent → 主人）落权威行后**调用** `bump_owner(KIND_NOTIFICATION, ...)`；
   **汇报面**（自分身 → 主人，OwnerLoopback）走汇报卡、**绝不** bump 通知 revision。
2. `compute_owner_notification_revision` 指纹语义正确：无通知→'empty'；落行后非空且随内容变化。

`bump_owner` 的 push 本就 best-effort（测试环境无在线节点，返回 pushed=0 不抛），故此处用 spy 只观测
「是否被 emit 以正确 kind 调到」，不依赖真实 WS 推送。直接打真实本地 PostgreSQL（端口 15432）；
不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.service import sync_invalidate_service
from backend.app.hasn.service.sync_invalidate_service import (
    EMPTY_NOTIFICATION_REVISION,
    KIND_NOTIFICATION,
    OWNER_KINDS,
    compute_owner_notification_revision,
)
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
    tag = uuid.uuid4().hex[:10]
    return f'h_{tag}', f'a_{tag}'


@pytest.fixture
def bump_spy(monkeypatch):
    """把 sync_invalidate_service.bump_owner 换成记录调用的 wrapper。

    emit() 的 _bump_notification_revision 在调用时 `from sync_invalidate_service import bump_owner`，
    绑定的是模块当前属性 → monkeypatch 模块属性即可被 spy 命中；wrapper 仍调真实实现（真算 revision、
    真走 best-effort push），保证测的是真实链路而非桩。"""
    calls: list[tuple[str, str]] = []
    real = sync_invalidate_service.bump_owner

    async def _spy(kind, db, owner_id):
        calls.append((kind, owner_id))
        return await real(kind, db, owner_id)

    monkeypatch.setattr(sync_invalidate_service, 'bump_owner', _spy)
    return calls


# ==================== KIND 注册 ====================


async def test_kind_notification_registered_as_owner_kind():
    # 模块级 pytestmark=asyncio，故用 async def 避免「非 async 却标 asyncio」告警。
    assert KIND_NOTIFICATION == 'notification'
    assert KIND_NOTIFICATION in OWNER_KINDS


# ==================== 指纹语义 ====================


async def test_revision_empty_then_changes_with_notifications(session):
    owner, _ = _ids()
    actor, _ = _ids()

    # 无通知 → 稳定空指纹
    assert await compute_owner_notification_revision(session, owner) == EMPTY_NOTIFICATION_REVISION

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
    rev1 = await compute_owner_notification_revision(session, owner)
    assert rev1 != EMPTY_NOTIFICATION_REVISION

    # 再来一条不同通知 → 指纹变化（daemon 据此判「有新东西」拉取）
    await NotificationService.emit(
        session,
        recipient_id=owner,
        source={'kind': 'user', 'id': actor, 'display_name': '路人乙'},
        category='social',
        type='new_follower',
        title='路人乙 关注了你',
        payload={'link': '/contacts'},
    )
    await session.flush()
    rev2 = await compute_owner_notification_revision(session, owner)
    assert rev2 != rev1


# ==================== emit → bump 接线 ====================


async def test_notice_plane_emit_bumps_notification_kind(session, bump_spy):
    """外部事件落通知面权威行 → emit 调 bump_owner(KIND_NOTIFICATION, owner)。"""
    owner, _ = _ids()
    actor, _ = _ids()
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

    notif_bumps = [owner_id for kind, owner_id in bump_spy if kind == KIND_NOTIFICATION]
    assert notif_bumps == [owner]


async def test_owner_loopback_emit_does_not_bump_notification(session, bump_spy):
    """自分身→主人（汇报面）：走汇报卡、不落通知中心 → 绝不 bump 通知 revision。"""
    owner, agent = _ids()
    session.add(HasnAgents(hasn_id=agent, owner_id=owner, display_name='小星', agent_name='xiaoxing'))
    await session.flush()

    await NotificationService.emit(
        session,
        recipient_id=owner,
        source={'kind': 'agent', 'id': agent, 'on_behalf_of': owner, 'display_name': '小星'},
        category='agent',
        type='task.done',
        title='任务已完成',
        payload={'link': f'/tasks/sessions/{uuid.uuid4().hex[:8]}'},
    )
    await session.flush()

    assert [kind for kind, _ in bump_spy if kind == KIND_NOTIFICATION] == []
