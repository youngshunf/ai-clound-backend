"""知识库创建业务幂等的真实 PostgreSQL 与真实 RAGFlow 验证。"""

from __future__ import annotations

import asyncio
import uuid

from pathlib import Path

import pytest
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_knowledge.manifest import KNOWLEDGE_AI_NATIVE_MANIFEST
from backend.app.hasn_knowledge.model import Kb
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[3]
_MIGRATION = _REPO / 'backend/sql/hasn_knowledge/migrations/2026-07-28-kb-create-idempotency.sql'


async def test_create_kb_manifest_exposes_optional_idempotency_key() -> None:  # noqa: RUF029
    """Agent 建库契约公开可选业务幂等键。"""
    capability = next(
        item
        for item in KNOWLEDGE_AI_NATIVE_MANIFEST['capabilities']
        if item['mcp_name'] == 'hasn.knowledge.create_kb'
    )
    schema = capability['input_schema']

    assert 'client_request_id' in schema['properties']
    assert 'client_request_id' not in schema['required']


async def _apply_migration(session) -> None:  # noqa: ANN001
    """使用 asyncpg simple-query 协议执行多语句迁移。"""
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    await raw.driver_connection.execute(_MIGRATION.read_text(encoding='utf-8'))


async def test_kb_create_migration_is_repeatable_and_adds_owner_unique_key(session) -> None:
    """迁移可重复执行，并建立 Owner 范围业务幂等唯一键。"""
    await _apply_migration(session)
    await _apply_migration(session)

    column_count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='hasn_knowledge' AND table_name='kb' "
                "AND column_name='client_request_id'"
            )
        )
    ).scalar_one()
    index_count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname='hasn_knowledge' AND tablename='kb' "
                "AND indexname='uq_kb_owner_client_request'"
            )
        )
    ).scalar_one()

    assert column_count == 1
    assert index_count == 1


async def test_retry_create_kb_reuses_one_real_dataset(session, ragflow_ready) -> None:
    """同 Owner 同幂等键重试只创建一个真实知识库与一个 RAGFlow dataset。"""
    await _apply_migration(session)
    tag = uuid.uuid4().hex[:12]
    owner = f'h_idem_{tag}'
    request_id = f'growth:test:{tag}:knowledge'
    created: dict | None = None

    try:
        created = await knowledge_service.create_kb(
            session,
            owner,
            name=f'幂等知识库-{tag}',
            description='真实幂等验证',
            client_request_id=request_id,
        )
        replay = await knowledge_service.create_kb(
            session,
            owner,
            name=f'幂等知识库-{tag}',
            description='真实幂等验证',
            client_request_id=request_id,
        )

        assert created['id'] == replay['id']
        assert created['idempotent_replay'] is False
        assert replay['idempotent_replay'] is True
        assert (
            await session.execute(
                sa.text(
                    'SELECT count(*) FROM hasn_knowledge.kb '
                    'WHERE owner_id=:owner AND client_request_id=:request_id'
                ),
                {'owner': owner, 'request_id': request_id},
            )
        ).scalar_one() == 1

        with pytest.raises(errors.ConflictError) as exc_info:
            await knowledge_service.create_kb(
                session,
                owner,
                name='篡改后的名称',
                description='真实幂等验证',
                client_request_id=request_id,
            )
        assert exc_info.value.data == {'error_code': 'KNOWLEDGE_IDEMPOTENCY_CONFLICT'}
    finally:
        if created is not None:
            await knowledge_service.delete_kb(session, owner, int(created['id']))


async def test_concurrent_create_kb_reuses_one_real_dataset(ragflow_ready) -> None:
    """并发重放在调用真实 RAGFlow 前串行化，不遗留第二个外部 dataset。"""
    tag = uuid.uuid4().hex[:12]
    owner = f'h_concurrent_{tag}'
    request_id = f'growth:concurrent:{tag}:knowledge'
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def create_and_commit() -> dict:
        async with maker() as db:
            result = await knowledge_service.create_kb(
                db,
                owner,
                name=f'并发知识库-{tag}',
                description='真实并发幂等验证',
                client_request_id=request_id,
            )
            await db.commit()
            return result

    created: dict | None = None
    try:
        first, second = await asyncio.gather(create_and_commit(), create_and_commit())
        created = first
        assert first['id'] == second['id']
        assert {first['idempotent_replay'], second['idempotent_replay']} == {False, True}
        async with maker() as db:
            assert (
                await db.execute(
                    sa.text(
                        'SELECT count(*) FROM hasn_knowledge.kb '
                        'WHERE owner_id=:owner AND client_request_id=:request_id'
                    ),
                    {'owner': owner, 'request_id': request_id},
                )
            ).scalar_one() == 1
    finally:
        if created is not None:
            async with maker() as db:
                await knowledge_service.delete_kb(db, owner, int(created['id']))
                # delete_kb 为生产软删；测试已真实删除外部 dataset 后硬删自己的专用行，避免污染共享测试库。
                await db.execute(
                    sa.delete(Kb).where(
                        Kb.owner_id == owner,
                        Kb.client_request_id == request_id,
                    )
                )
                await db.commit()
        await engine.dispose()
