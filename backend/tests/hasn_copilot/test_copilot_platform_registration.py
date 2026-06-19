"""会议副驾（copilot，模块「桌面端潜行会议副驾」）工作台平台接入 真实测试（零 mock）。

覆盖把会议副驾注册成工作台应用（WorkbenchApp + ``ensure_catalog_seeded`` 播种 ``hasn_app_catalog``）：
- WorkbenchApp 形态（local_tool / 非自动挂载 manual / 原生 webui 路由 ``/workbench/apps/copilot`` / ui_kind=None）。
- 会议副驾 **无 Agent 工具、无 AI-Native tool manifest、无 scope**（走工作会话派发，同 knowledge/community）。
- 真实 PG：``ensure_catalog_seeded`` 播种 copilot 行（local_tool/builtin/free/manual/sort 60，status=published
  → 工作台应用网格才出现「会议副驾」入口）；AppCollab default_agent_type=meeting_copilot（打开默认承接会议副驾分身）。

事实源: docs/hasn-node设计文档/桌面端潜行会议副驾/实施/01-分阶段实施计划与施工清单.md（P2/P6）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded
from backend.app.hasn.service.workbench_app_registry import workbench_app_registry
from backend.app.hasn_copilot.manifest import build_copilot_workbench_app
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def test_copilot_workbench_app_shape() -> None:
    """WorkbenchApp：local_tool + 非自动挂载（manual）+ 原生 webui 路由 /workbench/apps/copilot。"""
    app = build_copilot_workbench_app()
    assert app.id == 'copilot'
    assert app.name == '会议副驾'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/workbench/apps/copilot'
    assert app.ui_kind is None  # 原生 webui 页（模式 A），非 sidecar iframe


def test_copilot_registered_in_workbench_registry() -> None:
    """copilot 已进 workbench_app_registry（ensure_catalog_seeded 据此播种）。"""
    ids = {a.id for a in workbench_app_registry.list()}
    assert 'copilot' in ids, '会议副驾必须在 workbench_app_registry 注册，否则工作台应用网格不显示'


# ============================ 真实 PostgreSQL ============================


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.select(1))
            # AppCollab 两列幂等兜底（与 test_app_catalog_default_agent 同范式，使 schema 与 model 对齐）。
            await conn.execute(
                sa.text('ALTER TABLE hasn_app_catalog ADD COLUMN IF NOT EXISTS default_agent_type VARCHAR(64)')
            )
            await conn.execute(
                sa.text('ALTER TABLE hasn_app_catalog ADD COLUMN IF NOT EXISTS work_session_system_prompt TEXT')
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()  # 会话内变更回滚，不污染 dev DB
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_copilot_seeded_into_catalog(db: AsyncSession) -> None:
    """ensure_catalog_seeded 把 copilot 落 hasn_app_catalog（local_tool/builtin/free/manual/sort 60，published）。"""
    # 删后重播种 → 走「新插入」路径，断言静态默认表生效（不依赖既有迁移）。
    await db.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id == 'copilot'))
    await db.flush()

    await ensure_catalog_seeded(db)

    row = (
        await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'copilot'))
    ).scalars().first()
    assert row is not None, 'copilot catalog 行应被播种'
    assert row.source == 'builtin'
    assert row.status == 'published', '播种即上架，工作台应用网格才可见'
    assert row.execution_mode == 'local_tool'
    assert row.access_type == 'free'
    assert row.default_mount is False, '非自动挂载（install_policy=manual）'
    assert row.sort_order == 60
    assert row.entry_route == '/workbench/apps/copilot'
    assert 'personal' in (row.scope or [])
    assert row.manifest_present is True
    # AppCollab（doc21 §4.3）：打开会议副驾默认承接「会议副驾」分身（builtin_key=meeting_copilot）。
    assert row.default_agent_type == 'meeting_copilot'
    assert row.work_session_system_prompt
