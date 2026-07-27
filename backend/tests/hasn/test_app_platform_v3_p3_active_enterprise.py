"""应用平台 v3 P3「身份上下文瘦指针 active_enterprise_id」真实 PostgreSQL 测试（零 mock）。

验证 `get_active_workspace` / `switch_active_workspace` 从 `hasn_user_active_workspace`（已退役）
改读写 `hasn_owner_workbench_pref.active_enterprise_id` 后（设计 17 §4.2(1)/§10）：

- 默认无指针 → 个人上下文（`kind=personal`）。
- 切换到企业（有 approved 成员资格）→ 写 `active_enterprise_id`，`get` 返回 enterprise。
- 切回个人 → 清 `active_enterprise_id`。
- 切换到未加入企业 → ForbiddenError（不写指针）。
- **自愈**：指针指向已失去成员资格的企业 → `get` 复位个人并清指针（不泄漏越权上下文）。
- `_fallback_to_personal_if_active`（成员被移除 / 退出）→ 清当前指针。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/17-应用平台v3-去工作空间绑定与产物级协作.md §4.2(1)。

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
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.app.home.model import HasnOwnerWorkbenchPref
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


async def _seed_owner(db) -> tuple[int, str]:
    """造一个 owner（HasnHumans）+ 返回 (user_id, owner_hasn_id)。"""
    user_id = 940_000_000 + int(_uid(), 16) % 1_000_000
    owner_hasn_id = f'h_p3_{_uid()}'
    db.add(HasnHumans(
        hasn_id=owner_hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=f'P3_{owner_hasn_id[-8:]}',
    ))
    await db.flush()
    return user_id, owner_hasn_id


async def _seed_enterprise(db, *, owner_user_id: int, with_membership_for: int | None = None) -> int:
    ent = HasnEnterprise(name=f'E{_uid()}', slug=f'ent-{_uid()}', owner_user_id=owner_user_id, status='active')
    db.add(ent)
    await db.flush()
    if with_membership_for is not None:
        db.add(HasnEnterpriseMembership(
            enterprise_id=ent.id, user_id=with_membership_for, role='owner', status='approved',
        ))
        await db.flush()
    return int(ent.id)


async def _pref(db, owner_hasn_id: str) -> HasnOwnerWorkbenchPref | None:
    return (
        await db.execute(
            sa.select(HasnOwnerWorkbenchPref).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_hasn_id)
        )
    ).scalars().first()


async def test_active_enterprise_pointer_full_lifecycle(db) -> None:
    svc = workbench_domain_service

    # ① 默认无指针 → 个人。
    u1, owner1 = await _seed_owner(db)
    assert await svc.get_active_workspace(db, user_id=u1) == {'kind': 'personal', 'enterprise_id': None}

    # ② 切换到企业（有 approved 成员资格）→ 写指针 + get 返回 enterprise。
    ent1 = await _seed_enterprise(db, owner_user_id=u1, with_membership_for=u1)
    ws = await svc.switch_active_workspace(db, user_id=u1, kind='enterprise', enterprise_id=ent1)
    assert ws == {'kind': 'enterprise', 'enterprise_id': ent1}
    pref = await _pref(db, owner1)
    assert pref is not None and pref.active_enterprise_id == ent1
    assert await svc.get_active_workspace(db, user_id=u1) == {'kind': 'enterprise', 'enterprise_id': ent1}

    # ③ 切回个人 → 清指针。
    ws = await svc.switch_active_workspace(db, user_id=u1, kind='personal', enterprise_id=None)
    assert ws == {'kind': 'personal', 'enterprise_id': None}
    pref = await _pref(db, owner1)
    assert pref is not None
    assert pref.active_enterprise_id is None

    # ④ 切换到未加入企业 → ForbiddenError，不写指针。
    u2, _owner2 = await _seed_owner(db)
    ent2 = await _seed_enterprise(db, owner_user_id=u2, with_membership_for=u2)
    with pytest.raises(errors.ForbiddenError):
        await svc.switch_active_workspace(db, user_id=u1, kind='enterprise', enterprise_id=ent2)
    pref = await _pref(db, owner1)
    assert pref is not None
    assert pref.active_enterprise_id is None

    # ⑤ 自愈：指针指向已失去成员资格的企业 → get 复位个人并清指针。
    u3, owner3 = await _seed_owner(db)
    ent3 = await _seed_enterprise(db, owner_user_id=u3, with_membership_for=None)  # 无 approved 成员
    db.add(HasnOwnerWorkbenchPref(owner_hasn_id=owner3, active_enterprise_id=ent3))
    await db.flush()
    assert await svc.get_active_workspace(db, user_id=u3) == {'kind': 'personal', 'enterprise_id': None}
    pref = await _pref(db, owner3)
    assert pref is not None
    assert pref.active_enterprise_id is None

    # ⑥ _fallback_to_personal_if_active（成员被移除 / 退出）→ 清当前指针。
    u4, owner4 = await _seed_owner(db)
    ent4 = await _seed_enterprise(db, owner_user_id=u4, with_membership_for=u4)
    await svc.switch_active_workspace(db, user_id=u4, kind='enterprise', enterprise_id=ent4)
    pref = await _pref(db, owner4)
    assert pref is not None
    assert pref.active_enterprise_id == ent4
    await svc._fallback_to_personal_if_active(db, user_id=u4, enterprise_id=ent4)
    pref = await _pref(db, owner4)
    assert pref is not None
    assert pref.active_enterprise_id is None
