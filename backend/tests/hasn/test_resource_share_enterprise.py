"""应用平台 v3 P1：企业分支真实 PG 测试（admin 兜底 / 企业可见 / enterprise grantee）。

构造真实 hasn_humans + hasn_enterprise + membership 行（引用无 human 的真实 sys_user id，
满足 membership→sys_user FK），跑有效权限判定的企业三条分支。flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnEnterprise, HasnEnterpriseMembership, HasnHumans
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


async def _free_user_ids(session, n: int) -> list[int]:
    """取 n 个还没有 hasn_humans 映射的真实 sys_user id（满足 membership→sys_user FK + humans.user_id 唯一）。"""
    rows = (
        await session.execute(
            text('SELECT id FROM sys_user WHERE id NOT IN (SELECT user_id FROM hasn_humans) ORDER BY id LIMIT :n').bindparams(n=n)
        )
    ).all()
    return [int(r[0]) for r in rows]


async def test_enterprise_admin_and_visibility(session) -> None:
    tag = uuid.uuid4().hex[:8]
    uids = await _free_user_ids(session, 3)
    if len(uids) < 3:
        pytest.skip('无足够空闲 sys_user 构造企业成员，跳过')
    u_owner, u_admin, u_member = uids
    h_owner, h_admin, h_member = f'h_o_{tag}', f'h_ad_{tag}', f'h_m_{tag}'

    session.add_all([
        HasnHumans(hasn_id=h_owner, star_id=f's1{tag}', user_id=u_owner, nickname='owner', status='active'),
        HasnHumans(hasn_id=h_admin, star_id=f's2{tag}', user_id=u_admin, nickname='admin', status='active'),
        HasnHumans(hasn_id=h_member, star_id=f's3{tag}', user_id=u_member, nickname='member', status='active'),
    ])
    ent = HasnEnterprise(name=f'测试企业{tag}', slug=f'ent-{tag}', owner_user_id=u_owner, status='active')
    session.add(ent)
    await session.flush()
    eid = ent.id

    session.add_all([
        HasnEnterpriseMembership(enterprise_id=eid, user_id=u_owner, role='member', status='approved'),
        HasnEnterpriseMembership(enterprise_id=eid, user_id=u_admin, role='admin', status='approved'),
        HasnEnterpriseMembership(enterprise_id=eid, user_id=u_member, role='member', status='approved'),
    ])
    await session.flush()

    owner = Subject.human(h_owner)
    admin = Subject.human(h_admin)
    member = Subject.human(h_member)
    outsider = Subject.human(f'h_out_{tag}')

    # owner 在企业上下文建 deck（私有）
    deck = await deck_service.create_deck(
        session, owner_id=h_owner, title='企业方案', owner_scope='enterprise', enterprise_id=eid, visibility='private'
    )
    did = deck['id']
    assert deck['owner_scope'] == 'enterprise'

    # 私有：成员看不到，企业 admin 兜底 manager，局外人看不到
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=member, deck_id=did)
    got_admin = await deck_service.get_deck(session, subject=admin, deck_id=did)
    assert got_admin['my_permission'] == 'manager'  # admin_grant 兜底（决策③）
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=outsider, deck_id=did)

    # owner 一键团队共享 → visibility=enterprise
    await deck_service.set_visibility(session, subject=owner, deck_id=did, visibility='enterprise')
    got_member = await deck_service.get_deck(session, subject=member, deck_id=did)
    assert got_member['my_permission'] == 'viewer'  # visibility_grant
    # 成员 viewer 不能改
    page = await deck_service.create_page(session, subject=owner, deck_id=did, position=0, html='v1')
    with pytest.raises(errors.ForbiddenError):
        await deck_service.update_page(session, subject=member, page_id=page['id'], fields={'html': 'x'})

    # 局外人仍看不到（非成员）
    with pytest.raises(errors.NotFoundError):
        await deck_service.get_deck(session, subject=outsider, deck_id=did)


async def test_enterprise_grantee_share_bumps_members(session) -> None:
    """enterprise grantee 显式授 editor → 该企业成员获 editor（高于 visibility 的 viewer）。"""
    tag = uuid.uuid4().hex[:8]
    uids = await _free_user_ids(session, 2)
    if len(uids) < 2:
        pytest.skip('无足够空闲 sys_user，跳过')
    u_owner, u_member = uids
    h_owner, h_member = f'h_o2_{tag}', f'h_m2_{tag}'
    session.add_all([
        HasnHumans(hasn_id=h_owner, star_id=f'sa{tag}', user_id=u_owner, nickname='o', status='active'),
        HasnHumans(hasn_id=h_member, star_id=f'sb{tag}', user_id=u_member, nickname='m', status='active'),
    ])
    ent = HasnEnterprise(name=f'企业2{tag}', slug=f'ent2-{tag}', owner_user_id=u_owner, status='active')
    session.add(ent)
    await session.flush()
    eid = ent.id
    session.add_all([
        HasnEnterpriseMembership(enterprise_id=eid, user_id=u_owner, role='member', status='approved'),
        HasnEnterpriseMembership(enterprise_id=eid, user_id=u_member, role='member', status='approved'),
    ])
    await session.flush()

    owner = Subject.human(h_owner)
    member = Subject.human(h_member)
    deck = await deck_service.create_deck(
        session, owner_id=h_owner, title='企业可编辑', owner_scope='enterprise', enterprise_id=eid, visibility='private'
    )
    did = deck['id']
    page = await deck_service.create_page(session, subject=owner, deck_id=did, position=0, html='v1')

    # 给整个企业授 editor
    await deck_service.add_share(session, subject=owner, deck_id=did, grantee_type='enterprise', grantee_id=str(eid), permission='editor')
    got = await deck_service.get_deck(session, subject=member, deck_id=did)
    assert got['my_permission'] == 'editor'
    # 成员现在可改
    edited = await deck_service.update_page(session, subject=member, page_id=page['id'], fields={'html': 'team-edit'})
    assert edited['html'] == 'team-edit'
