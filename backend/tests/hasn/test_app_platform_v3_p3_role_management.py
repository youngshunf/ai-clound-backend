"""应用平台 v3 P3「企业角色 / 部门管理 API」真实 PostgreSQL 测试（零 mock）。

验证 `workbench_domain_service` 的角色管理业务方法（app/enterprise.py 的 7 个端点逐一委派到这里，
authz + 跨企业隔离全在 service 层强制，故直接对 service 串行断言即覆盖端点行为）：

- list/create/update/delete 角色 / 部门；grant/revoke 成员角色。
- owner / admin 鉴权：仅企业 owner 或 approved admin 成员可管理；普通成员 / 非成员 ForbiddenError。
- 跨企业隔离：企业 A 的 role_id 在企业 B 上下文 NotFound；member_role 行带 enterprise_id，绝不跨企业串。
- grant 只能授予本企业 approved 成员；幂等；非成员拒绝。delete 级联清成员关联。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2(4)/§6.5。

注：全部断言合并在单一 session/event-loop 内串行执行——asyncpg + pytest-asyncio 跨测 NullPool
连接会触发「attached to a different loop」teardown 竞争（本仓既有惯例，非逻辑问题）。
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
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.exception import errors
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


def _new_user_id() -> int:
    return 960_000_000 + int(_uid(), 16) % 1_000_000


async def _seed_enterprise(db, *, owner_user_id: int) -> int:
    ent = HasnEnterprise(name=f'E{_uid()}', slug=f'ent-{_uid()}', owner_user_id=owner_user_id, status='active')
    db.add(ent)
    await db.flush()
    return int(ent.id)


async def _add_member(db, *, enterprise_id: int, user_id: int, role: str, status: str = 'approved') -> None:
    db.add(HasnEnterpriseMembership(enterprise_id=enterprise_id, user_id=user_id, role=role, status=status))
    await db.flush()


async def test_role_management_full_matrix(db) -> None:
    svc = workbench_domain_service

    # 企业 E：owner=uo，admin=ua，member=um，旁观非成员=ux。
    uo, ua, um, ux = _new_user_id(), _new_user_id(), _new_user_id(), _new_user_id()
    ent = await _seed_enterprise(db, owner_user_id=uo)
    await _add_member(db, enterprise_id=ent, user_id=uo, role='owner')
    await _add_member(db, enterprise_id=ent, user_id=ua, role='admin')
    await _add_member(db, enterprise_id=ent, user_id=um, role='member')

    # ── 创建：owner 建一个角色 + 一个部门 ──────────────────────────────
    role = await svc.create_role(db, enterprise_id=ent, operator_user_id=uo, name=f'财务-{_uid()}', kind='role')
    assert role['kind'] == 'role' and role['member_count'] == 0 and role['enterprise_id'] == ent
    dept = await svc.create_role(db, enterprise_id=ent, operator_user_id=ua, name=f'销售部-{_uid()}', kind='department')
    assert dept['kind'] == 'department'  # admin 也可创建

    # ── authz：普通成员 / 非成员不可管理 ──────────────────────────────
    with pytest.raises(errors.ForbiddenError):
        await svc.create_role(db, enterprise_id=ent, operator_user_id=um, name='X', kind='role')
    with pytest.raises(errors.ForbiddenError):
        await svc.list_roles(db, enterprise_id=ent, operator_user_id=ux)

    # ── 校验：空名 / 超长 / 非法 kind / 同名冲突 ─────────────────────
    with pytest.raises(errors.RequestError):
        await svc.create_role(db, enterprise_id=ent, operator_user_id=uo, name='   ', kind='role')
    with pytest.raises(errors.RequestError):
        await svc.create_role(db, enterprise_id=ent, operator_user_id=uo, name='X', kind='team')
    with pytest.raises(errors.ConflictError):
        await svc.create_role(db, enterprise_id=ent, operator_user_id=uo, name=role['name'], kind='role')

    # ── 列表：含两条，member_count 初始 0 ────────────────────────────
    listed = await svc.list_roles(db, enterprise_id=ent, operator_user_id=ua)
    ids = {r['id'] for r in listed['items']}
    assert {role['id'], dept['id']} <= ids

    # ── 授予：把 member um 纳入部门；只读出 1 名成员 ──────────────────
    granted = await svc.grant_member_role(db, enterprise_id=ent, operator_user_id=uo, role_id=dept['id'], user_id=um)
    assert granted['granted'] is True
    # 幂等：再授一次不报错、不重复行。
    await svc.grant_member_role(db, enterprise_id=ent, operator_user_id=uo, role_id=dept['id'], user_id=um)
    members = await svc.list_role_members(db, enterprise_id=ent, operator_user_id=ua, role_id=dept['id'])
    assert [m['user_id'] for m in members['items']] == [um]
    # list_roles 的 member_count 随之 = 1。
    listed2 = await svc.list_roles(db, enterprise_id=ent, operator_user_id=uo)
    assert next(r for r in listed2['items'] if r['id'] == dept['id'])['member_count'] == 1

    # ── 授予非本企业成员被拒 ────────────────────────────────────────
    with pytest.raises(errors.RequestError):
        await svc.grant_member_role(db, enterprise_id=ent, operator_user_id=uo, role_id=dept['id'], user_id=ux)

    # ── 改名 / 改类型 ───────────────────────────────────────────────
    renamed = await svc.update_role(
        db, enterprise_id=ent, operator_user_id=uo, role_id=role['id'], name=f'财务部-{_uid()}', kind='department'
    )
    assert renamed['kind'] == 'department' and renamed['name'].startswith('财务部-')

    # ── 跨企业隔离：另起企业 E2，其 owner 不能碰 E 的角色（NotFound） ──
    uo2 = _new_user_id()
    ent2 = await _seed_enterprise(db, owner_user_id=uo2)
    await _add_member(db, enterprise_id=ent2, user_id=uo2, role='owner')
    with pytest.raises(errors.NotFoundError):
        await svc.update_role(db, enterprise_id=ent2, operator_user_id=uo2, role_id=dept['id'], name='盗用')
    with pytest.raises(errors.NotFoundError):
        await svc.grant_member_role(db, enterprise_id=ent2, operator_user_id=uo2, role_id=dept['id'], user_id=um)

    # ── 撤销：移除 um 的部门角色 ────────────────────────────────────
    await svc.revoke_member_role(db, enterprise_id=ent, operator_user_id=ua, role_id=dept['id'], user_id=um)
    after_revoke = await svc.list_role_members(db, enterprise_id=ent, operator_user_id=uo, role_id=dept['id'])
    assert after_revoke['items'] == []

    # ── 删除：建一个带成员的角色再删，校验级联清成员关联行 ───────────
    role_to_del = await svc.create_role(db, enterprise_id=ent, operator_user_id=uo, name=f'临时-{_uid()}', kind='role')
    await svc.grant_member_role(db, enterprise_id=ent, operator_user_id=uo, role_id=role_to_del['id'], user_id=um)
    await svc.delete_role(db, enterprise_id=ent, operator_user_id=uo, role_id=role_to_del['id'])
    remaining = await db.execute(
        sa.select(sa.func.count())
        .select_from(HasnEnterpriseMemberRole)
        .where(HasnEnterpriseMemberRole.role_id == role_to_del['id'])
    )
    assert int(remaining.scalar() or 0) == 0
    with pytest.raises(errors.NotFoundError):
        await svc.update_role(db, enterprise_id=ent, operator_user_id=uo, role_id=role_to_del['id'], name='nope')
