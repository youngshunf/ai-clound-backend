"""工作流历史权威投影真实 PostgreSQL 测试。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.schema.workflow_sync import WorkflowNodeRunsSyncRequest
from backend.app.hasn_task.service.workflow_history_service import workflow_history_service
from backend.app.hasn_task.service.workflow_sync_service import workflow_sync_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')
NODE_TABLES_SQL = (_SQL_DIR / '2026-07-14-workflow-node-tables.sql').read_text(encoding='utf-8')
ADVANCE_MODE_SQL = (_SQL_DIR / '2026-07-14-workflow-run-advance-mode.sql').read_text(encoding='utf-8')
WORKFLOW_HISTORY_SQL = (_SQL_DIR / '2026-07-26-workflow-history-recovery.sql').read_text(encoding='utf-8')


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def _run_sql(sql: str) -> None:
    import asyncpg

    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace(
        'postgresql+asyncpg://', 'postgresql://'
    )
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def env() -> AsyncIterator[SimpleNamespace]:
    """加载完整迁移链，并以事务隔离每个历史投影场景。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    await _run_sql(AINATIVE_SQL)
    await _run_sql(WORKFLOW_SQL)
    await _run_sql(NODE_TABLES_SQL)
    await _run_sql(ADVANCE_MODE_SQL)
    await _run_sql(WORKFLOW_HISTORY_SQL)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield SimpleNamespace(session=session, owner=f'h_history_{_uid()}', other_owner=f'h_other_{_uid()}')
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_orphan_run(
    db: AsyncSession,
    *,
    owner_id: str,
    status: str = 'failed',
    project_id: str | None = None,
) -> str:
    """仅写执行账本，不写父 workflow，用于复现定义已缺失的跨端历史。"""
    workflow_run_uuid = f'wfr_{_uid()}'
    workflow_uuid = f'wf_missing_{_uid()}'
    request = WorkflowNodeRunsSyncRequest.model_validate({
        'runs': [
            {
                'workflow_run_uuid': workflow_run_uuid,
                'workflow_uuid': workflow_uuid,
                'workflow_name_snapshot': '历史场景',
                'template_key_snapshot': 'one_person_company',
                'project_id': project_id,
                'status': status,
                'graph_snapshot': {
                    'nodes': [{'node_key': 'research'}, {'node_key': 'summary'}],
                    'edges': [['research', 'summary']],
                },
            }
        ],
        'node_runs': [
            {
                'node_run_uuid': f'ndr_{_uid()}',
                'workflow_run_uuid': workflow_run_uuid,
                'workflow_uuid': workflow_uuid,
                'node_key': 'research',
                'status': 'done',
                'output_summary': '调研完成',
                'artifacts': [{'artifact_id': f'art_{_uid()}', 'is_current': True}],
            },
            {
                'node_run_uuid': f'ndr_{_uid()}',
                'workflow_run_uuid': workflow_run_uuid,
                'workflow_uuid': workflow_uuid,
                'node_key': 'summary',
                'status': 'failed',
                'attention_reason': '等待外部资料',
            },
        ],
    })
    result = await workflow_sync_service.sync_node_runs(db, request, owner_id=owner_id)
    assert result.rejected == []
    return workflow_run_uuid


async def test_history_list_keeps_orphan_run_and_filters_project(env: SimpleNamespace) -> None:
    """父定义缺失时仍可按快照展示，且项目过滤不把 NULL 历史硬归类。"""
    project_id = str(uuid.uuid4())
    run_uuid = await _seed_orphan_run(env.session, owner_id=env.owner, project_id=project_id)
    await _seed_orphan_run(env.session, owner_id=env.owner, status='completed')

    page = await workflow_history_service.list_runs(
        env.session, owner_id=env.owner, status='all', project_id=project_id, limit=20
    )

    assert [item['workflow_run_id'] for item in page['items']] == [run_uuid]
    item = page['items'][0]
    assert item['workflow_name'] == '历史场景'
    assert item['template_key'] == 'one_person_company'
    assert item['project_id'] == project_id
    assert item['definition_state'] == 'missing'
    assert item['progress'] == {'done': 1, 'total': 2}
    assert item['capabilities']['can_mutate'] is False


async def test_history_detail_is_read_only_and_owner_scoped(env: SimpleNamespace) -> None:
    """云端详情可呈现孤儿账本，但明确禁止本节点接管远端执行。"""
    run_uuid = await _seed_orphan_run(env.session, owner_id=env.owner)

    detail = await workflow_history_service.get_scenario_view(
        env.session, owner_id=env.owner, workflow_run_uuid=run_uuid
    )

    assert detail['run']['workflow_run_id'] == run_uuid
    assert detail['run']['definition_state'] == 'missing'
    assert [node['node_key'] for node in detail['nodes']] == ['research', 'summary']
    assert detail['capabilities'] == {
        'can_mutate': False,
        'mutation_reason': 'remote_execution',
        'work_session_events': False,
    }
    assert detail['availability'] == {'work_session_events': 'unavailable_on_this_node'}
    with pytest.raises(errors.NotFoundError):
        await workflow_history_service.get_scenario_view(
            env.session, owner_id=env.other_owner, workflow_run_uuid=run_uuid
        )
