"""doc13 主会话跨会话感知·P1a 真实 PG 测试（零 mock）：owner-scoped 工作会话摘要读端点。

覆盖决策 D（跨设备读端点）+ 决策 G（云端源头预览上限，防御纵深）：
  1) `list_work_session_summaries` 只返 owner 名下 `summary_only` 工作会话（排除别的 owner、
     排除 conversation_visible），按末次活跃倒序，投影为 digest 精简摘要；
  2) `summary_preview` 超长时截到 `_SUMMARY_PREVIEW_CAP` 带省略号；
  3) `app` 从 `resource:<app>:<id>` origin_ref 推出；`status` 云端粗粒度→digest 词表；
  4) `get_work_session_summary` 命中返完整 summary + deep_link；不存在返 None；别的 owner 抛 403；
  5) 纯函数（_map_cloud_status / _app_from_origin / _work_session_summary_row）无需 DB 也正确。

需要本地 PostgreSQL（export DATABASE_PORT=15432）。不可达则 skip。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn.service.hasn_sessions_service import (
    _SUMMARY_PREVIEW_CAP,
    HasnSessionsService,
    _app_from_origin,
    _map_cloud_status,
    _work_session_summary_row,
    hasn_sessions_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _now_minus(seconds: int) -> datetime:
    return datetime.now(dt_timezone.utc) - timedelta(seconds=seconds)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            from sqlalchemy import select as _select

            await conn.execute(_select(1))
    except Exception as exc:  # noqa: BLE001 — 本地库不可达即跳过，非断言失败
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_list_work_session_summaries_owner_scoped_and_ordered(db) -> None:
    owner_id = f'h_owner_{_uid()}'
    other_owner = f'h_owner_{_uid()}'
    deck_id = uuid.uuid4().hex

    # 三个 summary_only 工作会话（不同末次活跃），一个 conversation_visible（应排除），
    # 一个别的 owner 的 summary_only（应排除）。
    newest = f'sess_ws_new_{_uid()}'
    mid = f'sess_ws_mid_{_uid()}'
    oldest = f'sess_ws_old_{_uid()}'
    db.add_all([
        HasnSessions(
            session_id=newest, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='app', origin_ref=f'resource:deck:{deck_id}', title='改产品发布 PPT',
            summary_checkpoint_json={'summary': '第 3 页大纲已写完', 'deep_link': f'hasn://deck/{deck_id}'},
            last_message_at=_now_minus(10),
        ),
        HasnSessions(
            session_id=mid, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='completed',
            origin_type='manual', origin_ref='manual-ref', title='整理知识库',
            summary_checkpoint_json={'summary': '已归档 12 篇'},
            last_message_at=_now_minus(100),
        ),
        HasnSessions(
            session_id=oldest, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='error',
            origin_type='workflow_run', origin_ref='workflow_run:9',
            summary_checkpoint_json={'summary': '第 2 步失败'},
            last_message_at=_now_minus(1000),
        ),
        # conversation_visible → 不是工作会话，应排除
        HasnSessions(
            session_id=f'sess_cv_{_uid()}', owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='interactive', session_scope='conversation_visible', session_status='active',
            origin_type='ui', last_message_at=_now_minus(5),
        ),
        # 别的 owner 的 summary_only → owner 隔离应排除
        HasnSessions(
            session_id=f'sess_other_{_uid()}', owner_id=other_owner, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='task_run', last_message_at=_now_minus(1),
        ),
    ])
    await db.flush()

    items = await hasn_sessions_service.list_work_session_summaries(db=db, owner_id=owner_id, limit=20)
    refs = [it['session_id'] for it in items]

    # owner 隔离 + 只 summary_only：只含本 owner 的 3 个工作会话。
    assert refs == [newest, mid, oldest], f'应按末次活跃倒序、只含本 owner 工作会话，实得 {refs}'

    top = items[0]
    assert top['topic'] == '改产品发布 PPT'
    assert top['app'] == 'deck', 'resource:deck:<id> → app=deck'
    assert top['status'] == 'running', 'active → running'
    assert top['summary_preview'] == '第 3 页大纲已写完'
    assert top['last_active'] > items[1]['last_active'] > items[2]['last_active']

    # 状态词表映射
    assert items[1]['status'] == 'completed'
    assert items[2]['status'] == 'failed', 'error → failed'


async def test_summary_preview_hard_capped(db) -> None:
    owner_id = f'h_owner_{_uid()}'
    long_text = '进' * 300  # 远超 _SUMMARY_PREVIEW_CAP
    sid = f'sess_long_{_uid()}'
    db.add(
        HasnSessions(
            session_id=sid, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='manual', summary_checkpoint_json={'summary': long_text},
            last_message_at=_now_minus(3),
        )
    )
    await db.flush()

    items = await hasn_sessions_service.list_work_session_summaries(db=db, owner_id=owner_id, limit=20)
    assert len(items) == 1
    preview = items[0]['summary_preview']
    assert preview.endswith('…')
    assert len(preview) == _SUMMARY_PREVIEW_CAP + 1, '截到上限 + 省略号'


async def test_get_work_session_summary(db) -> None:
    owner_id = f'h_owner_{_uid()}'
    other_owner = f'h_owner_{_uid()}'
    deck_id = uuid.uuid4().hex
    sid = f'sess_get_{_uid()}'
    db.add(
        HasnSessions(
            session_id=sid, owner_id=owner_id, hasn_id=f'a_{_uid()}',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='app', origin_ref=f'resource:deck:{deck_id}', title='做 PPT',
            summary_checkpoint_json={'summary': '完整进展文本', 'deep_link': f'hasn://deck/{deck_id}'},
            last_message_at=_now_minus(2),
        )
    )
    await db.flush()

    got = await hasn_sessions_service.get_work_session_summary(db=db, owner_id=owner_id, session_id=sid)
    assert got is not None
    assert got['session_id'] == sid
    assert got['summary'] == '完整进展文本'
    assert got['deep_link'] == f'hasn://deck/{deck_id}'
    assert got['app'] == 'deck'

    # 不存在 → None（云端不保证有本地工作会话行）
    missing = await hasn_sessions_service.get_work_session_summary(
        db=db, owner_id=owner_id, session_id=f'sess_missing_{_uid()}'
    )
    assert missing is None

    # 别的 owner 访问 → 403（try_get 归属校验）
    with pytest.raises(errors.ForbiddenError):
        await hasn_sessions_service.get_work_session_summary(db=db, owner_id=other_owner, session_id=sid)


def test_pure_helpers() -> None:
    # 状态词表
    assert _map_cloud_status('active') == 'running'
    assert _map_cloud_status('completed') == 'completed'
    assert _map_cloud_status('error') == 'failed'
    assert _map_cloud_status('cancelled') == 'cancelled'
    assert _map_cloud_status(None) == 'running'
    assert _map_cloud_status('weird') == 'weird'  # 未知原样透出

    # app 推断
    assert _app_from_origin('app', 'resource:deck:abc') == 'deck'
    assert _app_from_origin('app', 'resource:designsystem:ds_1') == 'designsystem'
    assert _app_from_origin('manual', 'manual-ref') == 'manual'  # 非 resource: → 回落 origin_type
    assert _app_from_origin('task_run', None) == 'task_run'

    # 行投影（无需 DB）
    row = _work_session_summary_row(
        HasnSessions(
            session_id='s1', owner_id='o1', hasn_id='a1',
            session_kind='task', session_scope='summary_only', session_status='active',
            origin_type='app', origin_ref='resource:deck:d1', title='t',
            summary_checkpoint_json={'summary': 'x'},
        )
    )
    assert row['session_id'] == 's1'
    assert row['app'] == 'deck'
    assert row['status'] == 'running'
    assert row['summary_preview'] == 'x'
    assert 'summary' not in row  # 行投影不含全文，全文只在 get_work_session_summary


async def test_upsert_get_roundtrip_service(db) -> None:
    # 端到端：用 service.upsert 造一个工作会话摘要，再用新读方法取回（同一 owner）。
    owner_id = f'h_owner_{_uid()}'
    sid = f'sess_rt_{_uid()}'
    await HasnSessionsService.upsert(
        db=db,
        session_data={
            'session_id': sid, 'owner_id': owner_id, 'hasn_id': f'a_{_uid()}',
            'session_kind': 'task', 'session_scope': 'summary_only', 'session_status': 'active',
            'origin_type': 'manual', 'title': '联系客户',
            'summary_checkpoint_json': {'summary': '已发出报价单'},
        },
        owner_id=owner_id,
    )
    await db.flush()

    listed = await hasn_sessions_service.list_work_session_summaries(db=db, owner_id=owner_id, limit=20)
    assert any(it['session_id'] == sid and it['summary_preview'] == '已发出报价单' for it in listed)
