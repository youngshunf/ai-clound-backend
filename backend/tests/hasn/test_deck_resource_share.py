"""应用平台 v3 P1：deck 产物级协作真实 PG 测试（零 mock）。

显式共享（human/agent grantee）+ 有效权限判定 + 页级乐观锁 + 列表「共享给我的」+ 撤销。
不依赖企业成员关系（无 membership 时 resolver 优雅返回空），覆盖 P1 核心「PPT 私有→指定人/分身可编辑」。
插入隔离测试行 → flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_deck.service.deck_service import Subject, deck_service
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


async def test_explicit_share_human_editor(session):
    """A 私有 deck → 共享给 B(editor) → B 可看可改不可删 → 撤销后 B 不可见。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')

    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='报价 PPT')
    deck_id = deck['id']
    assert deck['owner_scope'] == 'personal'
    assert deck['visibility'] == 'private'
    assert deck['my_permission'] == 'manager'

    page = await deck_service.create_page(session, subject=a, deck_id=deck_id, position=0, html='<h1>v1</h1>')
    page_id = page['id']

    # B 未被共享 → 不可见（按不存在处理，不泄露存在性）
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=b, deck_id=deck_id)

    # A 共享给 B editor
    await deck_service.add_share(session, subject=a, deck_id=deck_id, grantee_type='human', grantee_id=b.hasn_id, permission='editor')

    got = await deck_service.get_deck(session, subject=b, deck_id=deck_id)
    assert got['my_permission'] == 'editor'
    assert got['relation'] == 'shared'

    # B 可改页
    updated = await deck_service.update_page(session, subject=b, page_id=page_id, fields={'html': '<h1>v2</h1>'})
    assert updated['html'] == '<h1>v2</h1>'

    # B 不可删 deck（editor < manager）
    with pytest.raises(errors.ForbiddenError):
        await deck_service.delete_deck(session, subject=b, deck_id=deck_id)

    # 撤销后 B 不可见
    revoked = await deck_service.revoke_share(session, subject=a, deck_id=deck_id, grantee_type='human', grantee_id=b.hasn_id)
    assert revoked is True
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=b, deck_id=deck_id)


async def test_optimistic_lock_stale_version(session):
    """页级乐观锁：expected_version 不匹配 → 409 ConflictError。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='lock')
    page = await deck_service.create_page(session, subject=a, deck_id=deck['id'], position=0, html='v1')
    page_id = page['id']
    base_rev = page['rev']

    # 第一次带正确版本 → 成功，rev+1
    r1 = await deck_service.update_page(session, subject=a, page_id=page_id, fields={'html': 'v2'}, expected_version=base_rev)
    assert int(r1['rev']) == int(base_rev) + 1

    # 第二次仍用旧版本 → 冲突
    with pytest.raises(errors.ConflictError):
        await deck_service.update_page(session, subject=a, page_id=page_id, fields={'html': 'v3'}, expected_version=base_rev)


async def test_share_to_agent_can_edit(session):
    """共享给某分身（grantee=agent）→ 该分身工具可代操作；但删除需 manager 被拒。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    agent = Subject.agent(f'a_ag_{tag}', owner_hasn_id=f'h_other_{tag}')  # 别人的分身

    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='给分身改')
    deck_id = deck['id']
    page = await deck_service.create_page(session, subject=a, deck_id=deck_id, position=0, html='v1')

    # 未共享 → 分身不可见
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=agent, deck_id=deck_id)

    # 共享给该分身 editor
    await deck_service.add_share(session, subject=a, deck_id=deck_id, grantee_type='agent', grantee_id=agent.hasn_id, permission='editor')

    got = await deck_service.get_deck(session, subject=agent, deck_id=deck_id)
    assert got['my_permission'] == 'editor'

    edited = await deck_service.update_page(session, subject=agent, page_id=page['id'], fields={'html': 'by-agent'})
    assert edited['html'] == 'by-agent'

    # 分身只有 editor → 删除 deck 被拒
    with pytest.raises(errors.ForbiddenError):
        await deck_service.delete_deck(session, subject=agent, deck_id=deck_id)


async def test_list_accessible_includes_shared(session):
    """list_accessible_decks：B 的列表含「共享给我的」deck，relation=shared。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')

    own = await deck_service.create_deck(session, owner_id=b.hasn_id, title='B 自己的')
    shared = await deck_service.create_deck(session, owner_id=a.hasn_id, title='A 共享给 B 的')
    await deck_service.add_share(session, subject=a, deck_id=shared['id'], grantee_type='human', grantee_id=b.hasn_id, permission='viewer')

    listing = await deck_service.list_accessible_decks(session, subject=b)
    by_id = {it['id']: it for it in listing['items']}
    assert own['id'] in by_id and by_id[own['id']]['relation'] == 'owner'
    assert shared['id'] in by_id and by_id[shared['id']]['relation'] == 'shared'
    assert by_id[shared['id']]['my_permission'] == 'viewer'


async def test_viewer_cannot_edit(session):
    """viewer 只读：可 get/list_pages，但改页被拒。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='只读')
    page = await deck_service.create_page(session, subject=a, deck_id=deck['id'], position=0, html='v1')
    await deck_service.add_share(session, subject=a, deck_id=deck['id'], grantee_type='human', grantee_id=b.hasn_id, permission='viewer')

    # 可读
    await deck_service.list_pages(session, subject=b, deck_id=deck['id'])
    # 不可写
    with pytest.raises(errors.ForbiddenError):
        await deck_service.update_page(session, subject=b, page_id=page['id'], fields={'html': 'hack'})


async def test_manager_can_manage_shares(session):
    """被授 manager 的协作者可管理共享名单；editor 不可。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b = Subject.human(f'h_b_{tag}')
    c = Subject.human(f'h_c_{tag}')
    deck = await deck_service.create_deck(session, owner_id=a.hasn_id, title='manager 测试')
    # A 授 B manager
    await deck_service.add_share(session, subject=a, deck_id=deck['id'], grantee_type='human', grantee_id=b.hasn_id, permission='manager')
    # B(manager) 可再加 C
    await deck_service.add_share(session, subject=b, deck_id=deck['id'], grantee_type='human', grantee_id=c.hasn_id, permission='editor')
    shares = await deck_service.list_shares(session, subject=b, deck_id=deck['id'])
    grantees = {(s['grantee_type'], s['grantee_id']) for s in shares['shares']}
    assert ('human', b.hasn_id) in grantees
    assert ('human', c.hasn_id) in grantees
    # C(editor) 不可管理共享名单
    with pytest.raises(errors.ForbiddenError):
        await deck_service.list_shares(session, subject=c, deck_id=deck['id'])
