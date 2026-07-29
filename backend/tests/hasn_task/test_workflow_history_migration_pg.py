"""场景工作流历史迁移与同步的真实 PostgreSQL 测试。"""

from __future__ import annotations

import os

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.model import HasnWorkflowRun
from backend.app.hasn_task.schema.workflow_sync import WorkflowNodeRunsSyncRequest
from backend.app.hasn_task.service.workflow_sync_service import workflow_sync_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_MIGRATION = Path(__file__).parents[2] / 'sql/hasn_task/migrations/2026-07-26-workflow-history-recovery.sql'


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """在随机隔离 schema 中执行 migration，避免污染开发业务表。"""
    database_url = os.environ.get('WORKFLOW_HISTORY_TEST_DATABASE_URL', SQLALCHEMY_DATABASE_URL)
    test_schema = f'workflow_history_{uuid4().hex[:16]}'
    engine = create_async_engine(database_url, poolclass=NullPool)
    translated_engine = engine.execution_options(schema_translate_map={'hasn_task': test_schema})
    schema_created = False
    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(sa.select(1))
        except (OSError, sa.exc.SQLAlchemyError) as exc:
            pytest.skip(f'隔离 PostgreSQL 不可达，跳过: {exc!r}')

        async with engine.begin() as connection:
            await connection.execute(sa.text(f'CREATE SCHEMA {test_schema}'))
            schema_created = True
            await connection.execute(
                sa.text(
                    f"""
                    CREATE TABLE {test_schema}.workflow (
                        id bigserial PRIMARY KEY,
                        workflow_uuid varchar(64) NOT NULL UNIQUE,
                        owner_id varchar(64) NOT NULL DEFAULT '',
                        name varchar(200) NOT NULL DEFAULT '',
                        template_key varchar(64),
                        created_time timestamptz(6) NOT NULL DEFAULT now(),
                        updated_time timestamptz(6)
                    )
                    """
                )
            )
            await connection.execute(
                sa.text(
                    f"""
                    CREATE TABLE {test_schema}.workflow_run (
                        id bigserial PRIMARY KEY,
                        workflow_run_uuid varchar(64) NOT NULL UNIQUE,
                        workflow_uuid varchar(64) NOT NULL,
                        owner_id varchar(64) NOT NULL DEFAULT '',
                        scheduled_fire_at timestamptz(6),
                        dedupe_key varchar(160) NOT NULL UNIQUE,
                        status varchar(20) NOT NULL DEFAULT 'running',
                        advance_mode varchar(10) NOT NULL DEFAULT 'manual',
                        driver_node_id varchar(64),
                        lease_expires_at timestamptz(6),
                        graph_snapshot jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        output_summary text,
                        started_at timestamptz(6),
                        finished_at timestamptz(6),
                        created_time timestamptz(6) NOT NULL DEFAULT now(),
                        updated_time timestamptz(6)
                    )
                    """
                )
            )
            migration_sql = _MIGRATION.read_text(encoding='utf-8').replace('hasn_task', test_schema)
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            assert driver_connection is not None
            await driver_connection.execute(migration_sql)
            await driver_connection.execute(migration_sql)

        session = async_sessionmaker(translated_engine, expire_on_commit=False)()
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS {test_schema} CASCADE'))
        await engine.dispose()


async def test_migration_is_idempotent_and_sync_keeps_history_snapshots(db: AsyncSession) -> None:
    """新快照落库后，后续上行不得改写或清空它。"""
    owner_id = f'owner_{uuid4().hex[:16]}'
    project_id = uuid4()
    workflow_run_uuid = f'wfr_{uuid4().hex}'

    first = WorkflowNodeRunsSyncRequest.model_validate({
        'runs': [
            {
                'workflow_run_uuid': workflow_run_uuid,
                'workflow_uuid': f'wf_{uuid4().hex}',
                'workflow_name_snapshot': '新品发布场景',
                'template_key_snapshot': 'product_launch',
                'project_id': project_id,
            }
        ]
    })
    first_result = await workflow_sync_service.sync_node_runs(db, first, owner_id=owner_id)
    assert first_result.accepted_runs == 1

    replay = WorkflowNodeRunsSyncRequest.model_validate({
        'runs': [
            {
                'workflow_run_uuid': workflow_run_uuid,
                'workflow_uuid': first.runs[0].workflow_uuid,
                'workflow_name_snapshot': '错误重放名称',
                'template_key_snapshot': 'incorrect_replay',
                'project_id': uuid4(),
            }
        ]
    })
    replay_result = await workflow_sync_service.sync_node_runs(db, replay, owner_id=owner_id)
    assert replay_result.accepted_runs == 1

    legacy = WorkflowNodeRunsSyncRequest.model_validate({
        'runs': [{'workflow_run_uuid': workflow_run_uuid, 'workflow_uuid': first.runs[0].workflow_uuid}]
    })
    legacy_result = await workflow_sync_service.sync_node_runs(db, legacy, owner_id=owner_id)
    assert legacy_result.accepted_runs == 1

    stored = await db.scalar(sa.select(HasnWorkflowRun).where(HasnWorkflowRun.workflow_run_uuid == workflow_run_uuid))
    assert stored is not None
    assert stored.workflow_name_snapshot == '新品发布场景'
    assert stored.template_key_snapshot == 'product_launch'
    assert stored.project_id == project_id
