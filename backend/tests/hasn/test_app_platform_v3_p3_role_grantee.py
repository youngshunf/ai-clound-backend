"""应用平台 v3 P3「role grantee 解析」真实 PostgreSQL 测试（零 mock）。

验证 `resolve_effective_permission` 的 explicit_grant 第 4 步对 `grantee_type='role'` 的判定
（设计 17 §6.5/§4.2(4)）：

- 内置成员角色（grantee_id=`builtin:member|admin|owner`）：按主体 S 在 `resource_enterprise_id`
  的成员 role 推导，含包含关系 owner ⊇ admin ⊇ member。
- 自定义角色 / 部门（grantee_id=str(role_id)）：查 `hasn_enterprise_member_role`
  （user_id × enterprise_id），跨企业不串。
- 分身代主人：subject_kind='agent' 时按其主人的成员身份命中同一 role grant。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §6.5。

注：全部断言合并在单一 session/event-loop 内串行执行——asyncpg + pytest-asyncio 跨测
NullPool 连接会触发「attached to a different loop」teardown 竞争（本仓既有惯例，非逻辑问题）。
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_member_role import HasnEnterpriseMemberRole
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_enterprise_role import HasnEnterpriseRole
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.resource_share_service import resource_share_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_owner(db) -> tuple[int, str]:
    user_id = 950_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_role_{_uid()}'
    db.add(HasnHumans(hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'R_{owner_hasn_id[-8:]}'))
    await db.flush()
    return user_id, owner_hasn_id


async def _seed_enterprise(db, *, owner_user_id: int) -> int:
    ent = HasnEnterprise(name=f'E{_uid()}', slug=f'ent-{_uid()}', owner_user_id=owner_user_id, status='active')
    db.add(ent)
    await db.flush()
    return int(ent.id)


async def _add_member(db, *, enterprise_id: int, user_id: int, role: str) -> None:
    db.add(HasnEnterpriseMembership(enterprise_id=enterprise_id, user_id=user_id, role=role, status='approved'))
    await db.flush()


async def _share(db, *, rid: str, owner: str, grantee_id: str, permission: str) -> None:
    await resource_share_service.upsert_share(
        db,
        resource_type='deck',
        resource_id=rid,
        owner_hasn_id=owner,
        grantee_type='role',
        grantee_id=grantee_id,
        permission=permission,
        granted_by=owner,
    )
    await db.flush()


async def _resolve(db, *, subject_hasn_id, subject_owner, subject_kind, rid, owner, ent) -> str:
    return await resource_share_service.resolve_effective_permission(
        db,
        subject_hasn_id=subject_hasn_id,
        subject_kind=subject_kind,
        subject_owner_hasn_id=subject_owner,
        resource_type='deck',
        resource_id=rid,
        resource_owner_hasn_id=owner,
        resource_owner_scope='enterprise',
        resource_enterprise_id=ent,
        resource_visibility='private',
    )


async def test_role_grantee_full_matrix(db) -> None:
    # 企业 E：A=owner（产物归属者），B/D=member，C=admin。
    ua, owner_a = await _seed_owner(db)
    ub, owner_b = await _seed_owner(db)
    uc, owner_c = await _seed_owner(db)
    ud, owner_d = await _seed_owner(db)
    ent = await _seed_enterprise(db, owner_user_id=ua)
    await _add_member(db, enterprise_id=ent, user_id=ua, role='owner')
    await _add_member(db, enterprise_id=ent, user_id=ub, role='member')
    await _add_member(db, enterprise_id=ent, user_id=uc, role='admin')
    await _add_member(db, enterprise_id=ent, user_id=ud, role='member')

    # ① builtin:member 授 editor → 成员 B 命中 editor。
    r1 = f'd_{_uid()}'
    await _share(db, rid=r1, owner=owner_a, grantee_id='builtin:member', permission='editor')
    assert await _resolve(db, subject_hasn_id=owner_b, subject_owner=owner_b, subject_kind='human', rid=r1, owner=owner_a, ent=ent) == 'editor'

    # ② builtin:admin 授 manager → 仅 member 的 B 不命中（仍 none）。
    r2 = f'd_{_uid()}'
    await _share(db, rid=r2, owner=owner_a, grantee_id='builtin:admin', permission='manager')
    assert await _resolve(db, subject_hasn_id=owner_b, subject_owner=owner_b, subject_kind='human', rid=r2, owner=owner_a, ent=ent) == 'none'

    # ③ admin_grant 优先：企业 admin C 对企业产物恒 manager（离职兜底，§6.5），role grant 对其已被覆盖。
    assert await _resolve(db, subject_hasn_id=owner_c, subject_owner=owner_c, subject_kind='human', rid=r2, owner=owner_a, ent=ent) == 'manager'
    assert await _resolve(db, subject_hasn_id=owner_c, subject_owner=owner_c, subject_kind='human', rid=r1, owner=owner_a, ent=ent) == 'manager'

    # ④ 自定义部门：建「销售部」并把 B 纳入 → grantee_id=str(role_id) 授 viewer → B 命中 viewer。
    dept = HasnEnterpriseRole(enterprise_id=ent, name=f'销售部-{_uid()}', kind='department')
    db.add(dept)
    await db.flush()
    db.add(HasnEnterpriseMemberRole(enterprise_id=ent, user_id=ub, role_id=dept.id))
    await db.flush()
    r3 = f'd_{_uid()}'
    await _share(db, rid=r3, owner=owner_a, grantee_id=str(dept.id), permission='viewer')
    assert await _resolve(db, subject_hasn_id=owner_b, subject_owner=owner_b, subject_kind='human', rid=r3, owner=owner_a, ent=ent) == 'viewer'

    # ⑤ 部门精确匹配：同企业成员 D 未加入该部门 → 不命中 r3（custom role 不外溢给非部门成员）。
    assert await _resolve(db, subject_hasn_id=owner_d, subject_owner=owner_d, subject_kind='human', rid=r3, owner=owner_a, ent=ent) == 'none'

    # ⑥ 分身代主人：B 的分身（subject_kind=agent，主人=owner_b）命中 B 的成员 role grant（r1 editor）。
    agent_b = f'a_{_uid()}'
    assert await _resolve(db, subject_hasn_id=agent_b, subject_owner=owner_b, subject_kind='agent', rid=r1, owner=owner_a, ent=ent) == 'editor'
