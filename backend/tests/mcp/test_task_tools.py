"""平台工具 · task 域 真实 service 测试（禁 mock，TOOLMIG2-P1）。

验证从 hasn-node 本地 hasn-mcp 迁来的「纯云端代理」任务工具：
- 注册齐全（11 个），工具名/命名空间/execution_location/scope 与 manifest capabilities 1:1；
- scope split：读类无 scope；管理类 task:manage；触发类 task:run（与本地 hasn-mcp 跨仓对齐）；
- input_schema 关键必填项防回归；
- 真实 PG 往返：create(once)→get→list→pause→resume→run_now→delete（事务真提交，测试后清理该 owner 行）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_task_tools.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.task import TASK_TOOLS


def _tool(name: str):
    for t in TASK_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'task 工具未注册: {name}')


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_task_tools_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='任务测试分身',
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_task_tools_test',
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
    'hasn.task.create',
    'hasn.task.list',
    'hasn.task.get',
    'hasn.task.update',
    'hasn.task.pause',
    'hasn.task.resume',
    'hasn.task.delete',
    'hasn.task.run_now',
    'hasn.task.list_runs',
    'hasn.task.get_run',
    'hasn.task.query_results',
}

# approve/reject 是主人侧 D4 业务态（owner JWT 经 app 面）——绝不应作为 agent 工具暴露。
_OWNER_ONLY = {'hasn.task.approve', 'hasn.task.reject'}


def test_task_tools_register_exactly() -> None:
    """11 个纯代理工具全注册，且不含主人侧 approve/reject。"""
    names = {t.name for t in TASK_TOOLS}
    assert names == _EXPECTED_NAMES, f'差异: {names ^ _EXPECTED_NAMES}'
    assert not (names & _OWNER_ONLY), 'approve/reject 是主人侧业务态，不应迁到 agent 工具'


def test_task_tools_are_cloud_platform() -> None:
    """全部 source=platform、namespace=hasn.task、execution_location=cloud。"""
    for t in TASK_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.task'
        assert t.execution_location == 'cloud'


def test_task_tools_scope_split_read_vs_manage_vs_run() -> None:
    """读类无 scope；建/改/暂停/恢复/删=task:manage；立即执行=task:run（跨仓与本地 hasn-mcp 对齐）。"""
    reads = {'hasn.task.list', 'hasn.task.get', 'hasn.task.list_runs', 'hasn.task.get_run', 'hasn.task.query_results'}
    manage = {'hasn.task.create', 'hasn.task.update', 'hasn.task.pause', 'hasn.task.resume', 'hasn.task.delete'}
    for t in TASK_TOOLS:
        if t.name in reads:
            assert t.required_scopes == [], f'{t.name} 读类不应有 scope'
        elif t.name in manage:
            assert t.required_scopes == ['task:manage'], f'{t.name} 管理类应声明 task:manage'
        else:
            assert t.name == 'hasn.task.run_now'
            assert t.required_scopes == ['task:run'], 'run_now 应声明 task:run'


def test_required_fields_match_contract() -> None:
    """关键必填项与 manifest capabilities 一致。"""
    assert _tool('hasn.task.create').input_schema['required'] == ['name', 'prompt', 'schedule_type', 'schedule_config']
    assert _tool('hasn.task.get').input_schema['required'] == ['task_id']
    assert _tool('hasn.task.get_run').input_schema['required'] == ['run_id']
    # 读类 list 不应有 required
    assert 'required' not in _tool('hasn.task.list').input_schema


def test_task_scopes_in_aggregated_catalog() -> None:
    """task:manage/task:run 已登记 scope 展示目录（webui 能力管理可见可管控）。"""
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert SCOPE_CATALOG['task:manage']['domain'] == 'task'
    assert SCOPE_CATALOG['task:run']['domain'] == 'task'


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_task_lifecycle_roundtrip_real_db() -> None:
    """真实 PG：create(once)→get→list→pause→resume→run_now→delete。事务真提交，测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.database.db import async_db_session

    owner = f'h_task_tool_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner)
    try:
        # 1) 建一次性任务（once → scheduled，写类经 .begin() 真提交）
        created = await _tool('hasn.task.create').execute(
            ctx,
            {
                'name': '每日巡检',
                'prompt': '检查系统健康',
                'schedule_type': 'once',
                'schedule_config': {'run_at': '2099-01-01T08:00:00+08:00'},
            },
        )
        tid = created['task_id']
        assert created['name'] == '每日巡检'
        assert created['state'] == 'scheduled'
        assert created['created'] is True
        assert created['created_by_kind'] == 'agent'

        # 2) 读回（确认已落库、跨独立会话可见）
        got = await _tool('hasn.task.get').execute(ctx, {'task_id': tid})
        assert got['task_id'] == tid
        assert got['name'] == '每日巡检'

        # 3) 列表含它
        lst = await _tool('hasn.task.list').execute(ctx, {})
        assert any(t['task_id'] == tid for t in lst)

        # 4) 暂停（scheduled → paused）→ 恢复（paused → scheduled）
        paused = await _tool('hasn.task.pause').execute(ctx, {'task_id': tid})
        assert paused['state'] == 'paused'
        resumed = await _tool('hasn.task.resume').execute(ctx, {'task_id': tid})
        assert resumed['state'] == 'scheduled'

        # 5) 立即执行（scheduled → scheduled，next_run_at=now，由持 runtime 节点本地 tick 拾取）
        ran = await _tool('hasn.task.run_now').execute(ctx, {'task_id': tid})
        assert ran['state'] == 'scheduled'

        # 6) 列执行记录（新建任务无 run → 空列表，确定性读真打 DB）
        runs = await _tool('hasn.task.list_runs').execute(ctx, {'task_id': tid})
        assert runs == []

        # 7) 删除（软删 → 不再出现在列表）
        dele = await _tool('hasn.task.delete').execute(ctx, {'task_id': tid})
        assert dele == {'deleted': True}
        lst2 = await _tool('hasn.task.list').execute(ctx, {})
        assert not any(t['task_id'] == tid for t in lst2)
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_task.run_summary WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_task.task WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = :o'), {'o': owner})
