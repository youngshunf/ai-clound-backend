"""平台项目创建幂等契约的真实 PostgreSQL 测试。

覆盖图坊两阶段派发会话 A 的硬门：

- 迁移可重复执行，并建立 ``(owner_id, client_request_id)`` 唯一约束；
- 同一主人、同一请求键的顺序或并发重放只创建一个项目；
- 不同主人可复用同一请求键；
- 同一请求键携带不同创建参数时显式返回 409，不静默复用错误项目。
"""

from __future__ import annotations

import asyncio
import uuid

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_project.model import HasnProject
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.mcp.tools.project import PROJECT_TOOLS
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_MIGRATION = _REPO / 'backend/sql/hasn_project/migrations/2026-07-19-project-create-idempotency.sql'


async def test_agent_create_tool_exposes_optional_idempotency_key() -> None:  # noqa: RUF029
    create = next(tool for tool in PROJECT_TOOLS if tool.name == 'hasn.project.create')
    assert 'client_request_id' in create.input_schema['properties']
    assert 'client_request_id' not in create.input_schema['required']


async def _apply_migration(engine) -> None:  # noqa: ANN001
    """使用 asyncpg simple-query 协议执行多语句迁移。"""
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(_MIGRATION.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def pg() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过项目创建幂等测试：{exc!r}')

    await _apply_migration(engine)
    await _apply_migration(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owners: list[str] = []
    try:
        yield maker, owners
    finally:
        if owners:
            async with maker.begin() as db:
                await db.execute(sa.delete(HasnProject).where(HasnProject.owner_id.in_(owners)))
        await engine.dispose()


async def test_migration_is_idempotent_and_adds_owner_scoped_unique_key(
    pg: tuple[Any, list[str]],
) -> None:
    maker, _owners = pg
    async with maker() as db:
        column = (
            await db.execute(
                sa.text(
                    'SELECT data_type FROM information_schema.columns '
                    "WHERE table_schema='hasn_project' AND table_name='hasn_project' "
                    "AND column_name='client_request_id'"
                )
            )
        ).scalar_one()
        unique = (
            await db.execute(
                sa.text(
                    'SELECT count(*) FROM pg_indexes '
                    "WHERE schemaname='hasn_project' AND tablename='hasn_project' "
                    "AND indexname='uq_hasn_project_owner_client_request'"
                )
            )
        ).scalar_one()
    assert column == 'character varying'
    assert unique == 1


async def test_same_owner_and_request_key_replays_one_project(pg: tuple[Any, list[str]]) -> None:
    maker, owners = pg
    owner = f'h_img3_idem_{uuid.uuid4().hex[:12]}'
    owners.append(owner)
    request_id = f'img3:{uuid.uuid4()}'

    async with maker.begin() as db:
        first = await project_service.create_project(
            db,
            owner=owner,
            data={'name': '图坊项目', 'goal': '处理一组照片', 'client_request_id': request_id},
        )
        replay = await project_service.create_project(
            db,
            owner=owner,
            data={'name': '图坊项目', 'goal': '处理一组照片', 'client_request_id': request_id},
        )

    assert replay['id'] == first['id']
    assert first['client_request_id'] == request_id
    assert replay['idempotent_replay'] is True

    async with maker() as db:
        count = (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(HasnProject)
                .where(HasnProject.owner_id == owner, HasnProject.client_request_id == request_id)
            )
        ).scalar_one()
    assert count == 1


async def test_concurrent_replay_creates_one_project(pg: tuple[Any, list[str]]) -> None:
    maker, owners = pg
    owner = f'h_img3_race_{uuid.uuid4().hex[:12]}'
    owners.append(owner)
    request_id = f'img3:{uuid.uuid4()}'

    async def create_once() -> dict:
        async with maker.begin() as db:
            return await project_service.create_project(
                db,
                owner=owner,
                data={'name': '并发项目', 'client_request_id': request_id},
            )

    first, second = await asyncio.gather(create_once(), create_once())
    assert first['id'] == second['id']
    assert {first.get('idempotent_replay', False), second.get('idempotent_replay', False)} == {False, True}


async def test_same_key_isolated_by_owner_and_conflicting_payload_rejected(
    pg: tuple[Any, list[str]],
) -> None:
    maker, owners = pg
    owner_a = f'h_img3_owner_a_{uuid.uuid4().hex[:10]}'
    owner_b = f'h_img3_owner_b_{uuid.uuid4().hex[:10]}'
    owners.extend((owner_a, owner_b))
    request_id = f'img3:{uuid.uuid4()}'

    async with maker.begin() as db:
        project_a = await project_service.create_project(
            db, owner=owner_a, data={'name': '主人 A', 'client_request_id': request_id}
        )
        project_b = await project_service.create_project(
            db, owner=owner_b, data={'name': '主人 B', 'client_request_id': request_id}
        )
        with pytest.raises(errors.ConflictError) as exc:
            await project_service.create_project(
                db, owner=owner_a, data={'name': '被篡改名称', 'client_request_id': request_id}
            )

    assert project_a['id'] != project_b['id']
    assert exc.value.data == {'error_code': 'idempotency_conflict'}
