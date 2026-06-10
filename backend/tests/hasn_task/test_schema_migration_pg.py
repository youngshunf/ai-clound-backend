"""M1 任务系统 schema 迁移 + app/hasn_task 数据层 真实 PostgreSQL 测试（零 mock）。

覆盖：
- 迁移幂等：``2026-06-10-ainative-refactor.sql`` 可重复执行
- 五表落 ``hasn_task`` schema（public 不残留）
- 新列存在：task.continuation_enabled/enable_subagents/created_by_kind、
  run.continued_from_run_id、run_summary.continued_from_run_id/artifacts_json
- state CHECK 接受 ``pending_approval``/``rejected``、拒绝未知值
- 模型指向新 schema；owner CRUD（service 层）真实读写
- 新用户端 HTTP 面 /api/v1/hasn-task/app/tasks* owner 隔离

事实源: docs/hasn-node设计文档/12-任务系统实施方案/06-任务系统AI-Native应用化重构设计.md §2/§8；实施 91 M1。
"""

from __future__ import annotations

import uuid

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn_task.api.v1.app.run import router as task_run_app_router
from backend.app.hasn_task.api.v1.app.task import router as task_app_router
from backend.app.hasn_task.model import (
    HasnBuiltinTaskCatalog,
    HasnTask,
    HasnTaskAssignment,
    HasnTaskRun,
    HasnTaskRunSummary,
)
from backend.app.hasn_task.schema.task import CreateHasnTaskParam, DeleteHasnTaskParam
from backend.app.hasn_task.service.task_service import hasn_task_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

MIGRATION_SQL = (
    Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations' / '2026-06-10-ainative-refactor.sql'
)

TABLES = {'task', 'run', 'assignment', 'run_summary', 'builtin_catalog'}
OLD_PUBLIC_TABLES = {
    'hasn_task',
    'hasn_task_run',
    'hasn_task_assignment',
    'hasn_task_run_summary',
    'hasn_builtin_task_catalog',
}

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(task_app_router, prefix='/api/v1/hasn-task/app')
_APP.include_router(task_run_app_router, prefix='/api/v1/hasn-task/app')

_OWNER_A = 'hasn_owner_a_m1'
_OWNER_B = 'hasn_owner_b_m1'


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@pytest_asyncio.fixture
async def env() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session() -> AsyncIterator[AsyncSession]:  # noqa: RUF029
        yield session

    current_owner = {'hasn_id': _OWNER_A}

    async def _auth_inject(request: Request) -> str:  # noqa: RUF029  # FastAPI 依赖覆写需与原依赖同形
        request.scope['user'] = SimpleNamespace(id=900_100_001, hasn_id=current_owner['hasn_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, engine=engine, current_owner=current_owner)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _run_migration(engine: AsyncEngine) -> None:
    # 多语句 SQL 文件须走 asyncpg simple query 协议（prepared statement 不支持多命令）
    import asyncpg

    sql = MIGRATION_SQL.read_text(encoding='utf-8')
    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _column_names(session: AsyncSession, table: str) -> set[str]:
    rows = await session.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'hasn_task' AND table_name = :t"
        ),
        {'t': table},
    )
    return {r[0] for r in rows}


# ============================ 迁移 ============================


async def test_migration_idempotent_and_layout(env: SimpleNamespace) -> None:
    """迁移可重复执行；五表全部落 hasn_task schema 且 public 不残留；新列齐备。"""
    await _run_migration(env.engine)
    await _run_migration(env.engine)  # 幂等：第二次执行不报错

    rows = await env.session.execute(
        sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'hasn_task'")
    )
    in_schema = {r[0] for r in rows}
    assert in_schema >= TABLES, f'hasn_task schema 缺表: {TABLES - in_schema}'

    rows = await env.session.execute(
        sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {'names': list(OLD_PUBLIC_TABLES)},
    )
    leftovers = {r[0] for r in rows}
    assert not leftovers, f'public 残留旧任务表: {leftovers}'

    task_cols = await _column_names(env.session, 'task')
    assert {'continuation_enabled', 'enable_subagents', 'created_by_kind'} <= task_cols
    run_cols = await _column_names(env.session, 'run')
    assert 'continued_from_run_id' in run_cols
    summary_cols = await _column_names(env.session, 'run_summary')
    assert {'continued_from_run_id', 'artifacts_json'} <= summary_cols


async def test_state_check_accepts_new_business_states(env: SimpleNamespace) -> None:
    """state CHECK 收 pending_approval/rejected；未知值仍被拒绝。"""
    await _run_migration(env.engine)

    for state in ('pending_approval', 'rejected'):
        await env.session.execute(
            sa.text(
                'INSERT INTO hasn_task.task (owner_id, agent_id, name, prompt, schedule_type, schedule_config, state, '
                'skill_bundle_ids, skill_bundle_refs, skill_ids, skill_refs, workflow, task_uuid, created_time) '
                "VALUES (:o, 'agent_x', :n, 'p', 'interval', '{}'::jsonb, :s, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, "
                "'[]'::jsonb, '{}'::jsonb, :u, now())"
            ),
            {'o': _OWNER_A, 'n': f'm1-state-{state}', 's': state, 'u': f'tuuid-{_uid()}'},
        )
    await env.session.rollback()

    with pytest.raises(Exception, match='chk_hasn_task_state'):
        await env.session.execute(
            sa.text(
                'INSERT INTO hasn_task.task (owner_id, agent_id, name, prompt, schedule_type, schedule_config, state, '
                'skill_bundle_ids, skill_bundle_refs, skill_ids, skill_refs, workflow, task_uuid, created_time) '
                "VALUES (:o, 'agent_x', 'm1-bad-state', 'p', 'interval', '{}'::jsonb, 'totally_bogus', '[]'::jsonb, "
                "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, :u, now())"
            ),
            {'o': _OWNER_A, 'u': f'tuuid-{_uid()}'},
        )
    await env.session.rollback()


# ============================ 模型/数据层 ============================


async def test_models_target_hasn_task_schema() -> None:  # noqa: RUF029  # 与本套件统一 asyncio 形态
    """五个模型全部指向 hasn_task schema 的去前缀表名。"""
    expectations = {
        HasnTask: 'task',
        HasnTaskRun: 'run',
        HasnTaskAssignment: 'assignment',
        HasnTaskRunSummary: 'run_summary',
        HasnBuiltinTaskCatalog: 'builtin_catalog',
    }
    for model, table in expectations.items():
        assert model.__table__.schema == 'hasn_task', f'{model.__name__} schema 错: {model.__table__.schema}'
        assert model.__table__.name == table, f'{model.__name__} 表名错: {model.__table__.name}'


async def test_service_create_read_delete_roundtrip(env: SimpleNamespace) -> None:
    """service 层经新模型在真实 PG 写读删一条任务（含新字段）。"""
    await _run_migration(env.engine)

    name = f'm1-roundtrip-{_uid()}'
    obj = CreateHasnTaskParam(
        owner_id=_OWNER_A,
        agent_id='agent_m1',
        name=name,
        prompt='做点事',
        schedule_type='interval',
        schedule_config={'minutes': 60},
        task_uuid=f'tuuid-{_uid()}',
        continuation_enabled=True,
        enable_subagents=True,
        created_by_kind='owner',
    )
    task = await hasn_task_service.create_with_schedule(db=env.session, obj=obj)
    try:
        assert task.id > 0
        assert task.next_run_at is not None, 'create_with_schedule 应计算 next_run_at'
        fetched = await hasn_task_service.get(db=env.session, pk=task.id)
        assert fetched.name == name
        assert fetched.continuation_enabled is True
        assert fetched.enable_subagents is True
        assert fetched.created_by_kind == 'owner'
    finally:
        await hasn_task_service.delete(db=env.session, obj=DeleteHasnTaskParam(pks=[task.id]))
        await env.session.commit()


# ============================ HTTP 用户端面 ============================


def _data(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def test_app_surface_owner_isolation(env: SimpleNamespace) -> None:
    """新 /hasn-task/app 面：建任务归当前 owner；他人访问 → 404（不泄露）。"""
    await _run_migration(env.engine)

    payload = {
        'owner_id': 'will-be-overwritten',
        'agent_id': 'agent_m1',
        'name': f'm1-http-{_uid()}',
        'prompt': '每小时跑一次',
        'schedule_type': 'interval',
        'schedule_config': {'minutes': 60},
        'task_uuid': f'tuuid-{_uid()}',
        'continuation_enabled': True,
    }
    env.current_owner['hasn_id'] = _OWNER_A
    created = _data(await env.client.post('/api/v1/hasn-task/app/tasks', json=payload))
    task_id = created['task_id']
    try:
        detail = _data(await env.client.get(f'/api/v1/hasn-task/app/tasks/{task_id}'))
        assert detail['owner_id'] == _OWNER_A, '服务端必须以 JWT 身份覆写 owner_id'
        assert detail['continuation_enabled'] is True

        runs = _data(await env.client.get(f'/api/v1/hasn-task/app/tasks/{task_id}/runs'))
        assert runs['items'] == []

        # 跨户 → 404 不泄露
        env.current_owner['hasn_id'] = _OWNER_B
        resp = await env.client.get(f'/api/v1/hasn-task/app/tasks/{task_id}')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.json().get('code') == 404, f'跨户应 NotFound: {resp.text}'
    finally:
        env.current_owner['hasn_id'] = _OWNER_A
        resp = await env.client.delete(f'/api/v1/hasn-task/app/tasks/{task_id}')
        assert resp.status_code == 200
        await env.session.commit()
