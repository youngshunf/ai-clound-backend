"""多任务编排（工作流）N1 云端 schema + 数据层 真实 PostgreSQL 测试（零 mock）。

覆盖（实施 92 N1 验收）：
- 迁移幂等：``2026-06-11-workflow.sql`` 可重复执行
- workflow/workflow_edge/workflow_run 三表落 hasn_task schema
- task 加 workflow_uuid/node_key、run 加 workflow_run_uuid/node_key
- create_workflow 建菱形图（A→B、A→C、B&C→D）+ 节点 task + 边；get_workflow 返回节点+边
- 环检测拒绝 / 跨户分身拒绝（NotFound）/ node_key 重复拒绝 / 悬空边拒绝
- 纯图函数 detect_cycle / longest_chain_depth / validate_graph

事实源: docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §5/§9；实施 92 N1。
"""

from __future__ import annotations

import uuid

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn_task.schema.workflow import CreateWorkflowParam, WorkflowEdgeSpec, WorkflowNodeSpec
from backend.app.hasn_task.service import workflow_service as ws_mod
from backend.app.hasn_task.service.workflow_service import workflow_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')
NODE_TABLES_SQL = (_SQL_DIR / '2026-07-14-workflow-node-tables.sql').read_text(encoding='utf-8')
# P2 · W-S1 推进档位：workflow_run.advance_mode 列
ADVANCE_MODE_SQL = (_SQL_DIR / '2026-07-14-workflow-run-advance-mode.sql').read_text(encoding='utf-8')

_OWNER_A = 'hasn_owner_a_wf'
_OWNER_B = 'hasn_owner_b_wf'


def _uid() -> str:
    return uuid.uuid4().hex[:10]


async def _run_sql(sql: str) -> None:
    import asyncpg

    dsn = SQLALCHEMY_DATABASE_URL.render_as_string(hide_password=False).replace('postgresql+asyncpg://', 'postgresql://')
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

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield SimpleNamespace(session=session, engine=engine)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_agent(session: AsyncSession, *, owner_id: str, agent_id: str, name: str) -> None:
    session.add(
        HasnAgents(
            hasn_id=agent_id,
            star_id=f'{_uid()}#star',
            owner_id=owner_id,
            display_name=name,
            agent_name=name,
        )
    )
    await session.flush()


async def _column_names(session: AsyncSession, table: str) -> set[str]:
    rows = await session.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'hasn_task' AND table_name = :t"
        ),
        {'t': table},
    )
    return {r[0] for r in rows}


# ============================ 纯图函数（无需 DB） ============================


def test_detect_cycle_pure() -> None:
    keys = ['a', 'b', 'c', 'd']
    diamond = [('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')]
    assert ws_mod.detect_cycle(keys, diamond) is None

    cyclic = [('a', 'b'), ('b', 'c'), ('c', 'a')]
    cycle = ws_mod.detect_cycle(['a', 'b', 'c'], cyclic)
    assert cycle is not None
    assert set(cycle) == {'a', 'b', 'c'}


def test_longest_chain_depth_pure() -> None:
    keys = ['a', 'b', 'c', 'd']
    diamond = [('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')]
    assert ws_mod.longest_chain_depth(keys, diamond) == 3  # a→b→d / a→c→d


def test_validate_graph_rejects_dangling_and_self() -> None:
    nodes = [WorkflowNodeSpec(node_key='a', prompt='p'), WorkflowNodeSpec(node_key='b', prompt='p')]
    with pytest.raises(errors.RequestError, match='不存在'):
        ws_mod.validate_graph(nodes, [WorkflowEdgeSpec(parent='a', child='ghost')])
    with pytest.raises(errors.RequestError, match='依赖自己'):
        ws_mod.validate_graph(nodes, [WorkflowEdgeSpec(parent='a', child='a')])


# ============================ 迁移 ============================


async def test_workflow_migration_idempotent_and_columns(env: SimpleNamespace) -> None:
    await _run_sql(WORKFLOW_SQL)  # 第二次执行：幂等
    await _run_sql(NODE_TABLES_SQL)  # 节点专属表迁移二次执行：幂等
    await _run_sql(ADVANCE_MODE_SQL)  # advance_mode 迁移二次执行：幂等（可重复跑不报错）

    rows = await env.session.execute(
        sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'hasn_task'")
    )
    in_schema = {r[0] for r in rows}
    assert {'workflow', 'workflow_edge', 'workflow_run', 'workflow_node', 'workflow_node_run'} <= in_schema

    assert {'workflow_uuid', 'node_key'} <= await _column_names(env.session, 'task')
    assert {'workflow_run_uuid', 'node_key'} <= await _column_names(env.session, 'run')
    # 节点专属表关键列
    assert {'node_uuid', 'node_key', 'agent_id', 'is_origin', 'output_spec', 'review_policy'} <= (
        await _column_names(env.session, 'workflow_node')
    )
    assert {'node_run_uuid', 'workflow_run_uuid', 'node_key', 'status'} <= (
        await _column_names(env.session, 'workflow_node_run')
    )
    # P2 · W-S1：workflow_run 增 advance_mode 列
    assert 'advance_mode' in await _column_names(env.session, 'workflow_run')


async def test_workflow_run_advance_mode_default_manual(env: SimpleNamespace) -> None:
    """新建 workflow_run 未显式指定 advance_mode 时默认 'manual'（DB 默认 + list_runs 序列化带出）。"""
    agent_id = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=agent_id, name='分身')
    obj = CreateWorkflowParam(
        name=f'档位-{_uid()}',
        nodes=[_node('a', agent_id)],
        edges=[],
    )
    wf = await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    try:
        # 直接插入一条执行实例，故意不带 advance_mode → 落 DB 默认值
        run_uuid = f'wr_{_uid()}'
        await env.session.execute(
            sa.text(
                'INSERT INTO hasn_task.workflow_run '
                '(workflow_run_uuid, workflow_uuid, owner_id, dedupe_key, status) '
                'VALUES (:wru, :wu, :o, :dk, :st)'
            ),
            {
                'wru': run_uuid,
                'wu': wf.workflow_uuid,
                'o': _OWNER_A,
                'dk': f'{wf.workflow_uuid}:{_uid()}',
                'st': 'running',
            },
        )
        await env.session.flush()

        # DB 默认值 = 'manual'
        db_val = await env.session.execute(
            sa.text('SELECT advance_mode FROM hasn_task.workflow_run WHERE workflow_run_uuid = :wru'),
            {'wru': run_uuid},
        )
        assert db_val.scalar() == 'manual'

        # list_runs 序列化把 advance_mode 带出（owner/agent 面共用此序列化）
        runs = await workflow_service.list_runs(env.session, owner_id=_OWNER_A, workflow_uuid=wf.workflow_uuid)
        assert len(runs) == 1
        assert runs[0]['workflow_run_id'] == run_uuid
        assert runs[0]['advance_mode'] == 'manual'
    finally:
        await env.session.rollback()


# ============================ 建图 ============================


def _node(key: str, agent_id: str) -> WorkflowNodeSpec:
    return WorkflowNodeSpec(node_key=key, agent_id=agent_id, prompt=f'do {key}')


async def test_create_diamond_workflow_and_get(env: SimpleNamespace) -> None:
    """菱形图 A→B、A→C、B&C→D：建图 + 节点 task + 边；get 返回节点+边。"""
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=f'a_{_uid()}', name='主分身')
    research = f'a_{_uid()}'
    writer = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=research, name='研究分身')
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=writer, name='写作分身')

    obj = CreateWorkflowParam(
        name=f'迁移调研-{_uid()}',
        goal='调研 Postgres 迁移并产出决策备忘',
        schedule_type='once',
        nodes=[
            _node('plan', research),
            _node('research-cost', research),
            _node('research-perf', research),
            _node('synthesize', writer),
        ],
        edges=[
            WorkflowEdgeSpec(parent='plan', child='research-cost'),
            WorkflowEdgeSpec(parent='plan', child='research-perf'),
            WorkflowEdgeSpec(parent='research-cost', child='synthesize'),
            WorkflowEdgeSpec(parent='research-perf', child='synthesize'),
        ],
    )
    wf = await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    try:
        assert wf.id > 0
        assert wf.owner_id == _OWNER_A
        assert wf.status == 'active'
        assert wf.next_run_at is not None

        detail = await workflow_service.get_workflow(
            env.session, owner_id=_OWNER_A, workflow_uuid=wf.workflow_uuid
        )
        assert len(detail['nodes']) == 4
        node_keys = {n['node_key'] for n in detail['nodes']}
        assert node_keys == {'plan', 'research-cost', 'research-perf', 'synthesize'}
        # 节点是带 workflow_uuid 的 task
        for n in detail['nodes']:
            assert n['agent_id'] in {research, writer}
        assert len(detail['edges']) == 4
        assert {'parent': 'plan', 'child': 'research-cost'} in detail['edges']

        # P1 双写：workflow_node 专属表落对应节点行（行数 = 节点数，node_key 匹配）
        wn_rows = await env.session.execute(
            sa.text('SELECT node_key, agent_id FROM hasn_task.workflow_node WHERE workflow_uuid = :wu'),
            {'wu': wf.workflow_uuid},
        )
        wn = wn_rows.mappings().all()
        assert len(wn) == 4
        assert {r['node_key'] for r in wn} == {'plan', 'research-cost', 'research-perf', 'synthesize'}
        assert all(r['agent_id'] in {research, writer} for r in wn)

        # get_workflow 的 nodes 来自 workflow_node（含专属表特有字段 is_origin/display，task 投影没有）
        assert all('is_origin' in n and 'display' in n for n in detail['nodes'])

        # 跨户 get → NotFound
        with pytest.raises(errors.NotFoundError):
            await workflow_service.get_workflow(
                env.session, owner_id=_OWNER_B, workflow_uuid=wf.workflow_uuid
            )
    finally:
        await env.session.rollback()


async def test_create_workflow_rejects_cycle(env: SimpleNamespace) -> None:
    agent_id = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=agent_id, name='分身')
    obj = CreateWorkflowParam(
        name=f'环图-{_uid()}',
        nodes=[_node('a', agent_id), _node('b', agent_id)],
        edges=[WorkflowEdgeSpec(parent='a', child='b'), WorkflowEdgeSpec(parent='b', child='a')],
    )
    with pytest.raises(errors.RequestError, match='环'):
        await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    await env.session.rollback()


async def test_create_workflow_rejects_cross_owner_agent(env: SimpleNamespace) -> None:
    """节点 agent 属于 owner B → owner A 建图被拒（NotFound 不泄露）。"""
    foreign = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_B, agent_id=foreign, name='他人分身')
    obj = CreateWorkflowParam(
        name=f'跨户-{_uid()}',
        nodes=[_node('a', foreign)],
        edges=[],
    )
    with pytest.raises(errors.NotFoundError):
        await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    await env.session.rollback()


async def test_create_workflow_rejects_duplicate_node_key(env: SimpleNamespace) -> None:
    agent_id = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=agent_id, name='分身')
    obj = CreateWorkflowParam(
        name=f'重键-{_uid()}',
        nodes=[_node('dup', agent_id), _node('dup', agent_id)],
        edges=[],
    )
    with pytest.raises(errors.RequestError, match='重复'):
        await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    await env.session.rollback()


async def test_create_workflow_rejects_dangling_edge(env: SimpleNamespace) -> None:
    agent_id = f'a_{_uid()}'
    await _seed_agent(env.session, owner_id=_OWNER_A, agent_id=agent_id, name='分身')
    obj = CreateWorkflowParam(
        name=f'悬空边-{_uid()}',
        nodes=[_node('a', agent_id)],
        edges=[WorkflowEdgeSpec(parent='a', child='ghost')],
    )
    with pytest.raises(errors.RequestError, match='不存在'):
        await workflow_service.create_workflow(env.session, owner_id=_OWNER_A, obj=obj)
    await env.session.rollback()
