"""平台工具 · workflow 域 真实 service 测试（禁 mock，TOOLMIG2-P2）。

验证从 hasn-node 本地 hasn-mcp 迁来的「纯云端代理」工作流工具：
- 注册齐全（8 个），工具名/命名空间/execution_location/scope 与 manifest 1:1；
- 不暴露 add_node/add_edge（agent 经 create 一次声明整图）、不暴露 approve/reject（主人侧 D4）；
- scope split：读类无 scope；建/暂停/取消 workflow:manage；触发 workflow:run；
- 真实 PG 往返：create(once,1节点)→get→list→run→pause→cancel（事务真提交，测试后清理该 owner 行）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_workflow_tools.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.workflow import WORKFLOW_TOOLS


def _tool(name: str):
    for t in WORKFLOW_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'workflow 工具未注册: {name}')


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_workflow_tools_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='工作流测试分身',
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_workflow_tools_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ── 注册/契约（无需 DB）────────────────────────────────────────────────────────
_EXPECTED_NAMES = {
    'hasn.workflow.create',
    'hasn.workflow.list_agents',
    'hasn.workflow.get',
    'hasn.workflow.get_node_result',
    'hasn.workflow.run',
    'hasn.workflow.pause',
    'hasn.workflow.cancel',
    'hasn.workflow.list',
}

# 不暴露：add_node/add_edge（agent 经 create 一次声明整图）；approve/reject（主人侧 D4）。
_NOT_EXPOSED = {
    'hasn.workflow.add_node',
    'hasn.workflow.add_edge',
    'hasn.workflow.approve',
    'hasn.workflow.reject',
}


def test_workflow_tools_register_exactly() -> None:
    """8 个纯代理工具全注册，且不含 add_node/add_edge/approve/reject。"""
    names = {t.name for t in WORKFLOW_TOOLS}
    assert names == _EXPECTED_NAMES, f'差异: {names ^ _EXPECTED_NAMES}'
    assert not (names & _NOT_EXPOSED), 'add_node/add_edge/approve/reject 不应作为 agent 工具暴露'


def test_workflow_tools_are_cloud_platform() -> None:
    """全部 source=platform、namespace=hasn.workflow、execution_location=cloud。"""
    for t in WORKFLOW_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.workflow'
        assert t.execution_location == 'cloud'


def test_workflow_tools_scope_split() -> None:
    """读类无 scope；建/暂停/取消=workflow:manage；触发=workflow:run（跨仓与本地 hasn-mcp 对齐）。"""
    reads = {'hasn.workflow.list_agents', 'hasn.workflow.get', 'hasn.workflow.get_node_result', 'hasn.workflow.list'}
    manage = {'hasn.workflow.create', 'hasn.workflow.pause', 'hasn.workflow.cancel'}
    for t in WORKFLOW_TOOLS:
        if t.name in reads:
            assert t.required_scopes == [], f'{t.name} 读类不应有 scope'
        elif t.name in manage:
            assert t.required_scopes == ['workflow:manage'], f'{t.name} 管理类应声明 workflow:manage'
        else:
            assert t.name == 'hasn.workflow.run'
            assert t.required_scopes == ['workflow:run'], 'run 应声明 workflow:run'


def test_required_fields_match_contract() -> None:
    """关键必填项与 manifest capabilities 一致。"""
    assert _tool('hasn.workflow.create').input_schema['required'] == ['name', 'nodes']
    assert _tool('hasn.workflow.get').input_schema['required'] == ['workflow_id']
    assert _tool('hasn.workflow.get_node_result').input_schema['required'] == ['workflow_id', 'node_key']
    # 读类 list/list_agents 不应有 required
    assert 'required' not in _tool('hasn.workflow.list').input_schema
    assert 'required' not in _tool('hasn.workflow.list_agents').input_schema


def test_workflow_scopes_in_aggregated_catalog() -> None:
    """workflow:manage/workflow:run 已登记 scope 展示目录（domain=task，workflow 复用 task 域）。"""
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert SCOPE_CATALOG['workflow:manage']['domain'] == 'task'
    assert SCOPE_CATALOG['workflow:run']['domain'] == 'task'


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_workflow_lifecycle_roundtrip_real_db() -> None:
    """真实 PG：create(once,1节点)→get→list→run→pause→cancel。事务真提交，测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.database.db import async_db_session

    owner = f'h_wf_tool_{uuid.uuid4().hex[:18]}'
    agent_id = f'a_wf_tool_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner, agent_id)
    wf_id = None
    # create_workflow 会校验节点 agent 真实存在且属于 owner → seed 一个真实分身行（NOT NULL 无默认列齐全）
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                'INSERT INTO hasn_agents (hasn_id, star_id, owner_id, display_name, agent_name, api_key_hash) '
                'VALUES (:h, :s, :o, :dn, :an, :kh)'
            ),
            {'h': agent_id, 's': owner, 'o': owner, 'dn': '工作流测试分身', 'an': 'wftest', 'kh': 'x' * 64},
        )
    try:
        # 1) 建一次性单节点工作流（once → 非 pending_approval；写类经 .begin() 真提交）
        created = await _tool('hasn.workflow.create').execute(
            ctx,
            {
                'name': '调研流水线',
                'goal': '完成市场调研',
                'nodes': [{'node_key': 'research', 'prompt': '做一份调研', 'agent_id': agent_id}],
                'edges': [],
                'schedule_type': 'once',
            },
        )
        wf_id = created['workflow_id']
        assert created['name'] == '调研流水线'
        assert created['status'] != 'pending_approval'
        assert created['created_by_kind'] == 'agent'

        # 2) 查图（含节点 + 边 + 最近执行状态）
        got = await _tool('hasn.workflow.get').execute(ctx, {'workflow_id': wf_id})
        assert got['workflow']['workflow_id'] == wf_id
        assert any(n['node_key'] == 'research' for n in got['nodes'])

        # 3) 列表含它
        lst = await _tool('hasn.workflow.list').execute(ctx, {})
        assert any(w['workflow_id'] == wf_id for w in lst)

        # 4) 立即触发整图（→ active，next_run_at=now，由 driver 节点本地 fire）
        ran = await _tool('hasn.workflow.run').execute(ctx, {'workflow_id': wf_id})
        assert ran['status'] == 'active'

        # 5) 暂停（active → paused）
        paused = await _tool('hasn.workflow.pause').execute(ctx, {'workflow_id': wf_id})
        assert paused['status'] == 'paused'

        # 6) 取消（无运行中实例 → cancelled_runs=0，确定性）
        cancelled = await _tool('hasn.workflow.cancel').execute(ctx, {'workflow_id': wf_id})
        assert cancelled['workflow']['workflow_id'] == wf_id
        assert cancelled['cancelled_runs'] == 0
    finally:
        async with async_db_session.begin() as db:
            if wf_id:
                await db.execute(text('DELETE FROM hasn_task.workflow_run WHERE workflow_uuid = :w'), {'w': wf_id})
                await db.execute(text('DELETE FROM hasn_task.workflow_edge WHERE workflow_uuid = :w'), {'w': wf_id})
            await db.execute(text('DELETE FROM hasn_task.run_summary WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_task.task WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_task.workflow WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = :o'), {'o': owner})
