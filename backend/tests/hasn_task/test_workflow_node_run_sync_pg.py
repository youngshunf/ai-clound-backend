"""工作流执行态上行同步 真实 PostgreSQL 测试（零 mock，doc36 §6.3 · U5a）。

覆盖：
- 首次上行 INSERT + 重复上行 UPSERT 幂等（键 workflow_run_uuid / (workflow_run_uuid,node_key)）
- **本切片的正主**：`work_session_id` + `artifacts` 真落云端——doc36 §6.2 的跨节点产物聚合全靠这两列
- 端云状态域归一：daemon 仍在写的 success/error/pending_review → 云端十态（否则撞 CHECK 炸）
- 认不出的状态 → 逐条拒收，同批好行照落（不整批失败）
- 越权：别人的 run / 别人的 node_run 撞进来 → 拒收且**不改动**原行
- 时间字段：daemon 推的 Unix 秒真落成 timestamptz（联合类型不会被 Pydantic 自动转，易漏）
- 读侧闭环：既有 `hasn_workflow_node_run_dao.list_by_run` 能读到上行的行（此前查的是空表）

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/36-资源URI统一注册与分身可发现可编排设计.md §1.9/§6.3。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.crud.crud_workflow import (
    hasn_workflow_node_run_dao,
    hasn_workflow_run_dao,
)
from backend.app.hasn_task.schema.workflow_sync import (
    WorkflowNodeRunUpstream,
    WorkflowNodeRunsSyncRequest,
    WorkflowRunUpstream,
)
from backend.app.hasn_task.service.workflow_sync_service import (
    normalize_node_status,
    workflow_sync_service,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')
NODE_TABLES_SQL = (_SQL_DIR / '2026-07-14-workflow-node-tables.sql').read_text(encoding='utf-8')
ADVANCE_MODE_SQL = (_SQL_DIR / '2026-07-14-workflow-run-advance-mode.sql').read_text(encoding='utf-8')
WORKFLOW_HISTORY_SQL = (_SQL_DIR / '2026-07-26-workflow-history-recovery.sql').read_text(encoding='utf-8')


def _uid() -> str:
    return uuid.uuid4().hex[:10]


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
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
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
        yield SimpleNamespace(session=session, owner=f'h_own_wfsync_{_uid()}')
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _run(run_uuid: str, wf_uuid: str, **over: object) -> WorkflowRunUpstream:
    payload = {
        'workflow_run_uuid': run_uuid,
        'workflow_uuid': wf_uuid,
        'dedupe_key': f'{wf_uuid}:1',
        'status': 'running',
        'graph_snapshot': {'nodes': [{'node_key': 'research'}, {'node_key': 'summary'}], 'edges': []},
    }
    payload.update(over)
    return WorkflowRunUpstream(**cast(dict[str, Any], payload))


def _node(node_uuid: str, run_uuid: str, wf_uuid: str, node_key: str, **over: object) -> WorkflowNodeRunUpstream:
    payload = {
        'node_run_uuid': node_uuid,
        'workflow_run_uuid': run_uuid,
        'workflow_uuid': wf_uuid,
        'node_key': node_key,
        'status': 'running',
    }
    payload.update(over)
    return WorkflowNodeRunUpstream(**payload)


# ============================ 纯函数（无需 DB） ============================


def test_normalize_node_status_maps_scheduler_transitional_states() -> None:
    """daemon 调度器仍在写 success/error/pending_review，云端 CHECK 只认十态 → 必须归一。

    映射口径与 P1 迁移回填的 CASE 一致（success→done / error→failed），两处不能各说各话。
    """
    assert normalize_node_status('success') == 'done'
    assert normalize_node_status('error') == 'failed'
    assert normalize_node_status('pending_review') == 'running'
    # 十态原样透传
    for status in ('pending', 'ready', 'running', 'waiting', 'needs_attention',
                   'done', 'failed', 'skipped', 'stale', 'cancelled'):
        assert normalize_node_status(status) == status
    # 认不出的不硬塞（塞进去就是 CHECK 违例 → 整批 500）
    assert normalize_node_status('teleporting') is None
    assert normalize_node_status('') is None


# ============================ 真实 PG ============================


async def test_first_sync_inserts_run_and_node_runs(env: SimpleNamespace) -> None:
    """首次上行：run + node_run 落云端；work_session_id / artifacts 真到位。"""
    owner, db = env.owner, env.session
    wf, run_uuid = f'wf_{_uid()}', f'wfr_{_uid()}'
    ndr = f'ndr_{_uid()}'
    artifacts = [{'artifact_id': 'art_1', 'kind': 'document', 'is_current': True, 'version': 2}]

    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf)],
            node_runs=[
                _node(
                    ndr, run_uuid, wf, 'research',
                    status='success',
                    work_session_id='ws_research_42',
                    artifacts=artifacts,
                    output_summary='调研完成',
                )
            ],
        ),
        owner_id=owner,
    )
    assert res.rejected == []
    assert (res.accepted_runs, res.accepted_node_runs) == (1, 1)

    stored_run = await hasn_workflow_run_dao.get_by_uuid(db, run_uuid)
    assert stored_run is not None
    assert stored_run.owner_id == owner
    assert stored_run.graph_snapshot['nodes'][0]['node_key'] == 'research'

    nodes = await hasn_workflow_node_run_dao.list_by_run(db, run_uuid)
    assert len(nodes) == 1
    node = nodes[0]
    # 这两列是 doc36 §6.2 跨节点聚合的全部依据——它们不落，整条汇总链路就是空的
    assert node.work_session_id == 'ws_research_42'
    assert node.artifacts == artifacts
    assert node.status == 'done', 'daemon 的 success 必须归一到云端十态'
    assert node.output_summary == '调研完成'
    assert node.owner_id == owner


async def test_repeat_sync_is_idempotent_and_updates_in_place(env: SimpleNamespace) -> None:
    """重复上行不新增行、就地更新——daemon 每次节点状态变化都推，不幂等就会堆重复行。"""
    owner, db = env.owner, env.session
    wf, run_uuid, ndr = f'wf_{_uid()}', f'wfr_{_uid()}', f'ndr_{_uid()}'

    await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf)],
            node_runs=[_node(ndr, run_uuid, wf, 'research', work_session_id='ws_1')],
        ),
        owner_id=owner,
    )
    # 第二拍：节点跑完，带上产物与终态
    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf, status='completed')],
            node_runs=[
                _node(
                    ndr, run_uuid, wf, 'research',
                    status='done',
                    work_session_id='ws_1',
                    artifacts=[{'artifact_id': 'art_9', 'is_current': True}],
                )
            ],
        ),
        owner_id=owner,
    )
    assert res.rejected == []

    runs = (
        await db.execute(
            sa.text('SELECT count(*) FROM hasn_task.workflow_run WHERE workflow_run_uuid = :u'),
            {'u': run_uuid},
        )
    ).scalar_one()
    assert runs == 1
    stored_run = await hasn_workflow_run_dao.get_by_uuid(db, run_uuid)
    assert stored_run is not None
    assert stored_run.status == 'completed'

    nodes = await hasn_workflow_node_run_dao.list_by_run(db, run_uuid)
    assert len(nodes) == 1
    assert nodes[0].status == 'done'
    assert nodes[0].artifacts == [{'artifact_id': 'art_9', 'is_current': True}]


async def test_unix_timestamps_land_as_timestamptz(env: SimpleNamespace) -> None:
    """daemon SQLite 存 INTEGER Unix 秒，推上来必须落成 timestamptz。

    这条专门钉「联合类型不会被 Pydantic 自动转成 datetime」——smart union 下 int 会原样留着，
    service 不手转就静默丢时间（列全 NULL，看起来像 daemon 没推）。
    """
    owner, db = env.owner, env.session
    wf, run_uuid, ndr = f'wf_{_uid()}', f'wfr_{_uid()}', f'ndr_{_uid()}'
    started, finished = 1_760_000_000, 1_760_000_600

    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf, started_at=started, finished_at=finished)],
            node_runs=[_node(ndr, run_uuid, wf, 'research', status='done',
                             started_at=started, completed_at=finished)],
        ),
        owner_id=owner,
    )
    assert res.rejected == []

    stored_run = await hasn_workflow_run_dao.get_by_uuid(db, run_uuid)
    assert stored_run is not None
    assert stored_run.started_at == datetime.fromtimestamp(started, tz=UTC)
    node = (await hasn_workflow_node_run_dao.list_by_run(db, run_uuid))[0]
    assert node.started_time == datetime.fromtimestamp(started, tz=UTC)
    assert node.completed_time == datetime.fromtimestamp(finished, tz=UTC)


async def test_bad_row_rejected_without_dropping_the_good_ones(env: SimpleNamespace) -> None:
    """一条状态认不出的坏行只拒自己，同批好行照落（整批失败 = 好节点也永远上不来）。"""
    owner, db = env.owner, env.session
    wf, run_uuid = f'wf_{_uid()}', f'wfr_{_uid()}'
    good, bad = f'ndr_{_uid()}', f'ndr_{_uid()}'

    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf)],
            node_runs=[
                _node(good, run_uuid, wf, 'research', status='done'),
                _node(bad, run_uuid, wf, 'summary', status='teleporting'),
            ],
        ),
        owner_id=owner,
    )
    assert res.accepted_node_runs == 1
    assert len(res.rejected) == 1
    assert res.rejected[0]['node_run_uuid'] == bad
    assert 'teleporting' in res.rejected[0]['reason']

    nodes = await hasn_workflow_node_run_dao.list_by_run(db, run_uuid)
    assert [n.node_key for n in nodes] == ['research']


async def test_cross_owner_sync_is_rejected_and_leaves_row_untouched(env: SimpleNamespace) -> None:
    """别人的 run/node 撞进来 → 拒收，且原行**一字未改**（越权改写比读到更糟）。"""
    owner, db = env.owner, env.session
    intruder = f'h_own_wfsync_{_uid()}'
    wf, run_uuid, ndr = f'wf_{_uid()}', f'wfr_{_uid()}', f'ndr_{_uid()}'

    await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf)],
            node_runs=[_node(ndr, run_uuid, wf, 'research', work_session_id='ws_mine')],
        ),
        owner_id=owner,
    )

    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf, status='cancelled')],
            node_runs=[_node(ndr, run_uuid, wf, 'research', status='cancelled',
                             work_session_id='ws_hijacked')],
        ),
        owner_id=intruder,
    )
    assert (res.accepted_runs, res.accepted_node_runs) == (0, 0)
    assert len(res.rejected) == 2

    stored_run = await hasn_workflow_run_dao.get_by_uuid(db, run_uuid)
    assert stored_run is not None
    assert stored_run.owner_id == owner
    assert stored_run.status == 'running', '越权上行不得改写原主人的 run 状态'
    node = (await hasn_workflow_node_run_dao.list_by_run(db, run_uuid))[0]
    assert node.owner_id == owner
    assert node.work_session_id == 'ws_mine', '越权上行不得改写原主人的会话绑定'


async def test_backfilled_placeholder_row_converges_to_daemon_uuid(env: SimpleNamespace) -> None:
    """P1 迁移回填过的占位行（现生成的 ndr_ uuid，daemon 并不知道）会被上行收敛成权威 uuid。

    冲突键若按 node_run_uuid 走，这里会 INSERT 再撞 uq_workflow_node_run_key 炸掉——按语义键
    (workflow_run_uuid, node_key) 冲突才既幂等又能把存量占位行接管过来。
    """
    owner, db = env.owner, env.session
    wf, run_uuid = f'wf_{_uid()}', f'wfr_{_uid()}'
    placeholder, authoritative = f'ndr_{_uid()}', f'ndr_{_uid()}'

    # 模拟迁移回填：同 (run, node_key)、uuid 是云端自己生成的
    await db.execute(
        sa.text(
            'INSERT INTO hasn_task.workflow_node_run '
            '(node_run_uuid, workflow_run_uuid, workflow_uuid, owner_id, node_key, status) '
            'VALUES (:nr, :r, :w, :o, :k, :s)'
        ),
        {'nr': placeholder, 'r': run_uuid, 'w': wf, 'o': owner, 'k': 'research', 's': 'running'},
    )

    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            runs=[_run(run_uuid, wf)],
            node_runs=[_node(authoritative, run_uuid, wf, 'research', status='done',
                             work_session_id='ws_real')],
        ),
        owner_id=owner,
    )
    assert res.rejected == []
    assert res.accepted_node_runs == 1

    nodes = await hasn_workflow_node_run_dao.list_by_run(db, run_uuid)
    assert len(nodes) == 1, '不得留下占位行 + 新行两条'
    assert nodes[0].node_run_uuid == authoritative
    assert nodes[0].work_session_id == 'ws_real'


async def test_node_run_under_another_owners_run_is_rejected(env: SimpleNamespace) -> None:
    """父 run 属于别人 → 节点行不许挂进去（否则能往别人的 run 里塞节点）。"""
    owner, db = env.owner, env.session
    intruder = f'h_own_wfsync_{_uid()}'
    wf, run_uuid = f'wf_{_uid()}', f'wfr_{_uid()}'

    await workflow_sync_service.sync_node_runs(
        db, WorkflowNodeRunsSyncRequest(runs=[_run(run_uuid, wf)]), owner_id=owner
    )
    res = await workflow_sync_service.sync_node_runs(
        db,
        WorkflowNodeRunsSyncRequest(
            node_runs=[_node(f'ndr_{_uid()}', run_uuid, wf, 'injected', status='done')]
        ),
        owner_id=intruder,
    )
    assert res.accepted_node_runs == 0
    assert 'another owner' in res.rejected[0]['reason']
    assert await hasn_workflow_node_run_dao.list_by_run(db, run_uuid) == []
