"""WSPUSH-M2：配置/目录变更 WS 主动推送——写点 revision 一致性真实 PG 验收（零 mock）。

设计事实源：docs/hasn-node设计文档/02-数据与同步/07-配置与目录变更的WS主动推送与sync_agents轮询退役设计.md

核心不变量（写点 bump 推/缓存的 revision，必须 == daemon 拉取时各自数据源计算出的权威 revision）：
- platform_config：bump('platform_config') == update_config 返回的 revision（覆盖式写后一致）。
- common_skills：bump('common_skills') == get_common_skill_snapshot()[1]（委派一致）。
- builtin_catalog：compute_builtin_catalog_revision() 跑真实 schema 返回稳定指纹；
  插入一行 catalog（事务末尾回滚）→ 指纹变化。

事务末尾回滚，不留脏数据（update_config/插入仅 flush，不 commit）。需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import sync_invalidate_service as svc
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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


async def test_platform_config_bump_matches_update_config(session: AsyncSession) -> None:
    """bump('platform_config') 推/缓存的 revision == update_config 写后返回的权威 revision。"""
    current, _rev = await platform_default_config_service.get_effective_config(session)
    resp = await platform_default_config_service.update_config(session, config=current, updated_by='wspush-test')
    bumped = await svc.bump('platform_config', session)
    assert bumped == resp.revision, '推送的 platform_config revision 与写后权威 revision 不一致'


async def test_common_skills_bump_matches_snapshot(session: AsyncSession) -> None:
    """bump('common_skills') 委派 get_common_skill_snapshot，revision 必须一致。"""
    from backend.app.marketplace.service.common_skills_service import get_common_skill_snapshot

    _, snapshot_rev = await get_common_skill_snapshot(session)
    bumped = await svc.bump('common_skills', session)
    assert bumped == snapshot_rev


async def test_builtin_catalog_revision_real_schema_and_changes(session: AsyncSession) -> None:
    """compute_builtin_catalog_revision 跑真实 schema 返回稳定指纹；新增 catalog 行后指纹变化。"""
    before = await svc.compute_builtin_catalog_revision(session)
    assert isinstance(before, str) and before

    # 插入一行临时 catalog（事务末尾回滚，不污染目录）→ 指纹必须变化
    key = f'wspush_probe_{uuid.uuid4().hex[:8]}'
    session.add(
        HasnBuiltinTaskCatalog(
            builtin_key=key,
            name='WSPUSH 探针任务',
            schedule_type='cron',
            schedule_config={'cron': '0 9 * * *'},
            enabled=True,
            default_enabled=False,
            revision=1,
        )
    )
    await session.flush()
    after = await svc.compute_builtin_catalog_revision(session)
    assert after != before, '新增 catalog 行后 builtin_catalog_revision 未变化'
