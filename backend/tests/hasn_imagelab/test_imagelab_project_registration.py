"""图坊项目云端权威 ID 登记（IMG-P3-cloud，模块 14 doc30 §5.9 B1）owner 隔离数据层真实 PG 测试（零 mock）。

直接调 hasn_imagelab_project_service 落 hasn_imagelab_project 行 → flush（不 commit）→ 断言 → rollback。
覆盖 daemon ensure_cloud_project_registered 契约：
- 首次登记返回云端权威 ID（server_id，UUID 字符串）；
- 同一 owner 同一 local_ref 幂等复用返回同一个 id（daemon 重试/断线安全）；
- 跨 owner 隔离：同 local_ref 不同 owner 得不同 id，且一 owner 的登记对另一 owner 不可见（绝不跨 owner）。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/30-图像处理AI-Native应用(自研引擎·图坊)架构设计.md §5.9 B1；
      hasn-node apps/daemon/src/domains/imagelab/dispatch.rs::ensure_cloud_project_registered。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_imagelab.crud.crud_hasn_imagelab_project import hasn_imagelab_project_dao
from backend.app.hasn_imagelab.service.hasn_imagelab_project_service import hasn_imagelab_project_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


def _owner() -> str:
    return f'h_owner_{uuid.uuid4().hex[:12]}'


def _local_ref() -> str:
    return f'imgproj_{uuid.uuid4().hex[:20]}'


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    else:
        return True


async def test_first_registration_returns_server_id(session: AsyncSession) -> None:
    """首次登记 → 返回云端权威 ID（server_id，UUID 字符串），且落库可查回。"""
    owner = _owner()
    local_ref = _local_ref()

    server_id = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner, local_ref=local_ref, name='秋季海报批处理'
    )

    assert isinstance(server_id, str)
    assert _is_uuid(server_id), f'server_id 必须是 UUID 字符串（云端权威 ID），实得: {server_id!r}'

    # 落库可查回（owner 隔离键命中）
    row = await hasn_imagelab_project_dao.get_by_owner_and_local_ref(session, owner_id=owner, local_ref=local_ref)
    assert row is not None
    assert str(row.id) == server_id
    assert row.owner_id == owner
    assert row.local_ref == local_ref
    assert row.name == '秋季海报批处理'


async def test_idempotent_same_local_ref_returns_same_id(session: AsyncSession) -> None:
    """同一 owner 同一 local_ref 重复登记 → 幂等返回同一个 server_id，且不新增行。"""
    owner = _owner()
    local_ref = _local_ref()

    first = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner, local_ref=local_ref, name='项目A'
    )
    # 二次登记（daemon 缓存 miss / 断线重试场景），名字变化也不改权威 id
    second = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner, local_ref=local_ref, name='项目A-改名'
    )

    assert first == second, '同 owner 同 local_ref 必须幂等返回同一 server_id'

    # 库里只有一行（未因重试新增）
    rows = (
        await session.execute(
            select(hasn_imagelab_project_dao.model).where(
                hasn_imagelab_project_dao.model.owner_id == owner,
                hasn_imagelab_project_dao.model.local_ref == local_ref,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    # 名字更新已生效（不影响权威 id）
    assert rows[0].name == '项目A-改名'
    assert str(rows[0].id) == first


async def test_cross_owner_isolation(session: AsyncSession) -> None:
    """跨 owner 隔离：同 local_ref 不同 owner → 不同 server_id，且各自登记互不可见。"""
    owner_a = _owner()
    owner_b = _owner()
    shared_local_ref = _local_ref()  # 两台设备各自的本地 ULID 可能巧合相同——不得串号

    id_a = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner_a, local_ref=shared_local_ref, name='A的项目'
    )
    id_b = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner_b, local_ref=shared_local_ref, name='B的项目'
    )

    assert id_a != id_b, '不同 owner 即使 local_ref 相同也必须是不同的云端权威 ID'

    # owner_a 的登记键只命中 owner_a 自己的行（绝不跨 owner）
    row_a = await hasn_imagelab_project_dao.get_by_owner_and_local_ref(
        session, owner_id=owner_a, local_ref=shared_local_ref
    )
    assert row_a is not None
    assert str(row_a.id) == id_a
    assert row_a.owner_id == owner_a
    assert row_a.name == 'A的项目'

    # owner_b 查不到 owner_a 用不存在的 local_ref（隔离维度另测）——此处确保 b 命中自己
    row_b = await hasn_imagelab_project_dao.get_by_owner_and_local_ref(
        session, owner_id=owner_b, local_ref=shared_local_ref
    )
    assert row_b is not None
    assert str(row_b.id) == id_b
    assert row_b.owner_id == owner_b
    assert row_b.name == 'B的项目'

    # owner_a 用 owner_b 的（不存在于 a 名下的）某 local_ref 取不到——用一个 a 从未登记的 ref
    only_b_ref = _local_ref()
    id_only_b = await hasn_imagelab_project_service.register_project(
        db=session, owner_id=owner_b, local_ref=only_b_ref, name='仅B'
    )
    miss_for_a = await hasn_imagelab_project_dao.get_by_owner_and_local_ref(
        session, owner_id=owner_a, local_ref=only_b_ref
    )
    assert miss_for_a is None, 'owner_a 绝不能命中 owner_b 名下的登记'
    assert _is_uuid(id_only_b)
