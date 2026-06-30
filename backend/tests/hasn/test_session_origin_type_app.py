"""AppCollab AC-P2 真实 PG 兼容测试（零 mock）：origin_type=app 枚举 + resource:<app>:<id> 回指。

覆盖 doc21 §D3（实施 AC-P2 §测试）+ 2026-06-30 修复 A（chk_origin_type 加 manual/copilot）：
  1) `origin_type='app'` + `origin_ref='resource:deck:<id>'` 可落库（自由 VARCHAR，无枚举约束拒绝）；
  2) 旧枚举值（ui/task_run/external_app/workflow_run）不破——仍可落库与按 origin_type 过滤；
  3) `get_list_by_owner(origin_type='app')` 只返 app 会话；origin_ref 原样回读（resource: 回指）。
  4) 结果投影 `_projection_content_json` 把 origin_type/origin_ref 原样写进卡片（投影按 resource: 回指）。
  5) daemon 合法会话来源 manual（派发型工作会话）/copilot（会议副驾会话）upsert 后**原样保留**
     （修复 A：放宽约束 + 白名单同步两处，不再误压成 'system'）；真正未知漂移值仍兜底归一 'system'。

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
        # AC-P2 + 2026-06-30 迁移（IF EXISTS 幂等）放开 chk_origin_type 加 'app'/'manual'/'copilot'
        # ——测试自带 DDL 兜底，schema 与契约对齐（manual/copilot 是 daemon 合法会话来源，云端原样收下）。
        async with engine.begin() as conn:
            await conn.execute(select(1))
            await conn.execute(sa.text('ALTER TABLE hasn_sessions DROP CONSTRAINT IF EXISTS chk_origin_type'))
            await conn.execute(
                sa.text(
                    "ALTER TABLE hasn_sessions ADD CONSTRAINT chk_origin_type CHECK (origin_type IN "
                    "('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', "
                    "'app', 'manual', 'copilot'))"
                )
            )
    except Exception as exc:
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
    # 纯函数（无需 DB）：白名单值原样保留；**真正未知**的漂移/空值才归一为 'system'。
    for valid in ('ui', 'scheduler', 'task_run', 'workflow_run', 'external_app', 'api', 'system', 'app'):
        assert _normalize_origin_type(valid) == valid
    # 修复 A（福仔 2026-06-30 拍板）：manual（派发型工作会话）/copilot（会议副驾会话）是 daemon
    # 合法产出的会话来源，已加入 chk_origin_type 白名单——云端按权威**原样保留**，不再误压成 'system'。
    assert _normalize_origin_type('manual') == 'manual'
    assert _normalize_origin_type('copilot') == 'copilot'
    # 真正未知的漂移/空值仍兜底回落 'system'，杜绝单个非法枚举触发 CheckViolationError 打挂同步。
    assert _normalize_origin_type('bogus') == 'system'
    assert _normalize_origin_type('') == 'system'
    assert _normalize_origin_type(None) == 'system'


async def test_upsert_preserves_manual_origin_type_against_check_constraint(db) -> None:
    # 回归（真实 PG）：daemon B1 上推一个 origin_type='manual' 的派发型工作会话——
    # 修复前 chk_origin_type 白名单无 'manual' → CheckViolationError 把整批 summary 同步 500 掉、令上推无限重试。
    # 修复 A（福仔 2026-06-30 拍板）：放宽约束加 manual/copilot + 白名单同步两处，云端**原样保留** origin_type，
    # 既落库成功不再触发约束冲突，又不丢失会话来源语义（doc16 跨设备/单一云端记忆链依赖真实 origin_type）。
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
            'origin_type': 'manual',  # daemon 合法会话来源（派发型工作会话）
            'active_binding_id': 'bind_d3_000000001c',
            'title': '你现在来测试唤星AI 的消息发送工具',
        },
        owner_id=owner_id,
    )
    await db.flush()
    # 白名单合法值，约束放行且原样保留（不再误压成 'system'）。
    assert session.origin_type == 'manual'
    # 回读确认真的落库（CheckViolationError 不再发生），origin_type 忠实保留。
    persisted = (
        await db.execute(select(HasnSessions).where(HasnSessions.session_id == session_id))
    ).scalar_one()
    assert persisted.origin_type == 'manual'
    assert persisted.session_scope == 'summary_only'


async def test_upsert_falls_back_genuinely_unknown_origin_type(db) -> None:
    # 回归（真实 PG）：真正未知的漂移值（非白名单）仍兜底归一为 'system'，约束放行，
    # 杜绝任何非法枚举把整批工作会话 summary 同步 500 掉。
    owner_id = f'h_owner_{_uid()}'
    session_id = f'sess_bogus_{_uid()}'
    session = await HasnSessionsService.upsert(
        db=db,
        session_data={
            'session_id': session_id,
            'owner_id': owner_id,
            'hasn_id': f'a_{_uid()}',
            'session_kind': 'task',
            'session_scope': 'summary_only',
            'session_status': 'active',
            'origin_type': 'totally-bogus-drift',  # 真正未知漂移值
        },
        owner_id=owner_id,
    )
    await db.flush()
    assert session.origin_type == 'system'
    persisted = (
        await db.execute(select(HasnSessions).where(HasnSessions.session_id == session_id))
    ).scalar_one()
    assert persisted.origin_type == 'system'
