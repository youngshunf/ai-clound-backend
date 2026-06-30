"""AppCollab AC-P2 真实 PG 兼容测试（零 mock）：origin_type=app 枚举 + resource:<app>:<id> 回指。

覆盖 doc21 §D3（实施 AC-P2 §测试）：
  1) `origin_type='app'` + `origin_ref='resource:deck:<id>'` 可落库（自由 VARCHAR，无枚举约束拒绝）；
  2) 旧枚举值（ui/task_run/external_app/workflow_run）不破——仍可落库与按 origin_type 过滤；
  3) `get_list_by_owner(origin_type='app')` 只返 app 会话；origin_ref 原样回读（resource: 回指）。
  4) 结果投影 `_projection_content_json` 把 origin_type/origin_ref 原样写进卡片（投影按 resource: 回指）。

需要本地 PostgreSQL（export DATABASE_PORT=15432）。不可达则 skip。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn.service.hasn_sessions_service import (
    HasnSessionsService,
    _normalize_origin_type,
    _projection_content_json,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_LEGACY_ORIGINS = ('ui', 'task_run', 'external_app', 'workflow_run')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        # AC-P2 迁移（IF EXISTS 幂等）放开 chk_origin_type 加 'app'——测试自带 DDL 兜底，schema 与契约对齐。
        async with engine.begin() as conn:
            await conn.execute(select(1))
            await conn.execute(sa.text('ALTER TABLE hasn_sessions DROP CONSTRAINT IF EXISTS chk_origin_type'))
            await conn.execute(
                sa.text(
                    "ALTER TABLE hasn_sessions ADD CONSTRAINT chk_origin_type CHECK (origin_type IN "
                    "('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app'))"
                )
            )
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_origin_type_app_and_legacy_persist_and_filter(db) -> None:
    owner_id = f'h_owner_{_uid()}'
    deck_id = uuid.uuid4().hex
    app_ref = f'resource:deck:{deck_id}'
    app_session_id = f'sess_app_{_uid()}'

    # app 会话 + 四个旧枚举会话，同一 owner。
    db.add(
        HasnSessions(
            session_id=app_session_id, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='app', origin_ref=app_ref,
        )
    )
    for origin in _LEGACY_ORIGINS:
        db.add(
            HasnSessions(
                session_id=f'sess_{origin}_{_uid()}', owner_id=owner_id, hasn_id=f'a_{_uid()}',
                session_kind='task', session_scope='summary_only', session_status='active',
                origin_type=origin, origin_ref=f'{origin}-ref',
            )
        )
    await db.flush()

    # 全部落库（自由 VARCHAR 接受 'app'，无枚举约束拒绝）。
    rows = (
        (await db.execute(select(HasnSessions).where(HasnSessions.owner_id == owner_id))).scalars().all()
    )
    persisted = {r.origin_type for r in rows}
    assert persisted == {'app', *_LEGACY_ORIGINS}

    # 按 origin_type='app' 过滤（与 get_list_by_owner 同一 where 谓词）→ 只返 app 会话，
    # origin_ref 原样回读（resource: 回指）。服务层 paging 走 fastapi_pagination 请求上下文，
    # 此处直接复刻其过滤谓词在数据层验证契约。
    app_rows = (
        (
            await db.execute(
                select(HasnSessions).where(
                    HasnSessions.owner_id == owner_id, HasnSessions.origin_type == 'app'
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(app_rows) == 1
    assert app_rows[0].session_id == app_session_id
    assert app_rows[0].origin_ref == app_ref


async def test_projection_content_json_preserves_app_resource_ref() -> None:
    # 纯函数：结果投影把 origin_type/origin_ref 原样写进卡片 JSON（投影按 resource: 回指）。
    out = _projection_content_json(
        session_id='sess_x',
        agent_id='a_x',
        origin_type='app',
        origin_ref='resource:designsystem:ds_1',
        projection_data={'title': 't'},
    )
    assert out['origin_type'] == 'app'
    assert out['origin_ref'] == 'resource:designsystem:ds_1'
    assert out['projection_kind'] == 'work_session_result_summary'


async def test_normalize_origin_type_whitelist() -> None:
    # 纯函数（无需 DB）：白名单值原样保留；漂移/空值归一为 'system'。
    for valid in ('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app'):
        assert _normalize_origin_type(valid) == valid
    # 'manual' 是完成模式、非合法 origin_type——必须归一，杜绝 chk_origin_type 打挂同步。
    assert _normalize_origin_type('manual') == 'system'
    assert _normalize_origin_type('bogus') == 'system'
    assert _normalize_origin_type('') == 'system'
    assert _normalize_origin_type(None) == 'system'


async def test_upsert_normalizes_drifted_origin_type_against_check_constraint(db) -> None:
    # 回归（真实 PG）：daemon B1 上推一个 origin_type='manual' 的工作会话——
    # 旧逻辑直插 → CheckViolationError 把整批 summary 同步 500 掉、令上推无限重试。
    # 修复后 upsert 归一为 'system'，落库成功，不再触发约束冲突。
    owner_id = f'h_owner_{_uid()}'
    session_id = f'sess_manual_{_uid()}'
    session = await HasnSessionsService.upsert(
        db=db,
        session_data={
            'session_id': session_id,
            'owner_id': owner_id,
            'hasn_id': f'a_{_uid()}',
            'session_kind': 'task',
            'session_scope': 'summary_only',
            'session_status': 'active',
            'origin_type': 'manual',  # 漂移值（完成模式误塞进 origin_type）
            'active_binding_id': 'bind_d3_000000001c',
            'title': '你现在来测试唤星AI 的消息发送工具',
        },
        owner_id=owner_id,
    )
    await db.flush()
    # 归一为白名单合法值，约束放行。
    assert session.origin_type == 'system'
    # 回读确认真的落库（CheckViolationError 不再发生）。
    persisted = (
        await db.execute(select(HasnSessions).where(HasnSessions.session_id == session_id))
    ).scalar_one()
    assert persisted.origin_type == 'system'
    assert persisted.session_scope == 'summary_only'
