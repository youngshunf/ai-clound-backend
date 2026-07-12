"""G6 §15.9 规则二·授予上限（S7）：`upsert_share` 不得授出高于授予人自身档位。

form-invariant `rank(grant) ≤ rank(granter)`——被分享的 editor/viewer 不得把别人设成更高档
（防越权提档）。owner 直授路径 `granter_permission=None` 沿旧行为不校验（owner=manager 天然满足）。
§15.9 拍板：当前策略下「非 owner manager 可再分享 manager」自然满足规则二，无需额外服务端上限代码，
但 `grant ≤ own` 作为**形式不变量**与守卫保留在此——策略若收紧（如禁止平级提档）本测试立即兜住。

真 PG（零 mock）：flush 不 commit → 断言 → rollback。本地 PG 不可达则整文件 skip。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.resource_share_service import resource_share_service

# 导入即触发 deck adapter 模块级 register()——让 upsert_share 的 S2-5 fail-closed 校验对 'deck'
# 放行、进而命中规则二分支（否则 'deck' 未注册会先抛 ServerError 而非 ForbiddenError）。
from backend.app.hasn_deck.service import resource_adapter as _deck_adapter  # noqa: F401
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


async def test_grant_above_own_rank_forbidden(session) -> None:
    """editor 想授出 manager → 越权提档，拒绝（规则二）。"""
    tag = uuid.uuid4().hex[:8]
    with pytest.raises(errors.ForbiddenError):
        await resource_share_service.upsert_share(
            session,
            resource_type='deck',
            resource_id=f'deck_{tag}',
            owner_hasn_id=f'h_owner_{tag}',
            grantee_type='human',
            grantee_id=f'h_grantee_{tag}',
            permission='manager',
            granted_by=f'h_editor_{tag}',
            granter_permission='editor',
        )


async def test_grant_at_or_below_own_rank_ok(session) -> None:
    """granter=manager 授出 manager（等档）OK；granter=editor 授出 editor（等档）OK。"""
    tag = uuid.uuid4().hex[:8]
    row = await resource_share_service.upsert_share(
        session,
        resource_type='deck',
        resource_id=f'deck_{tag}',
        owner_hasn_id=f'h_owner_{tag}',
        grantee_type='human',
        grantee_id=f'h_g1_{tag}',
        permission='manager',
        granted_by=f'h_mgr_{tag}',
        granter_permission='manager',
    )
    assert row['permission'] == 'manager'

    row2 = await resource_share_service.upsert_share(
        session,
        resource_type='deck',
        resource_id=f'deck_{tag}',
        owner_hasn_id=f'h_owner_{tag}',
        grantee_type='human',
        grantee_id=f'h_g2_{tag}',
        permission='editor',
        granted_by=f'h_editor_{tag}',
        granter_permission='editor',
    )
    assert row2['permission'] == 'editor'


async def test_owner_direct_grant_no_ceiling(session) -> None:
    """granter_permission=None（owner 直授旧路径）→ 不校验上限，manager 照授。"""
    tag = uuid.uuid4().hex[:8]
    row = await resource_share_service.upsert_share(
        session,
        resource_type='deck',
        resource_id=f'deck_{tag}',
        owner_hasn_id=f'h_owner_{tag}',
        grantee_type='human',
        grantee_id=f'h_g_{tag}',
        permission='manager',
        granted_by=f'h_owner_{tag}',
        granter_permission=None,
    )
    assert row['permission'] == 'manager'
