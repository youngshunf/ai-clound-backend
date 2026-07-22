"""平台工具 · plan 域 真实 service 测试（禁 mock）。

验证从 hasn-node 本地 hasn-mcp 迁来的「纯云端代理」规划工具：
- 注册齐全（34 个 PURE_RELAY，含 PLAN-ENT 企业会议协同 invite/rsvp/availability
  与 PLAN-LOOP 里程碑改删 milestone.update/delete），
  工具名/命名空间/execution_location/scope 与原工具 1:1；
- input_schema 关键约束（priority 仍 string、id integer、必填项）防回归；
- 真实 PG 往返：goal/todo CRUD + capture/triage（事务真提交，测试后清理该 owner 行）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_plan_tools.py
无 DB 时跳过（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.plan import PLAN_TOOLS


def _tool(name: str):
    for t in PLAN_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'plan 工具未注册: {name}')


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_plan_tools_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_plan_tools_test',
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
    'hasn.plan.capture',
    'hasn.plan.triage',
    'hasn.plan.today',
    'hasn.plan.goal.list',
    'hasn.plan.goal.get',
    'hasn.plan.goal.create',
    'hasn.plan.goal.update',
    'hasn.plan.goal.delete',
    'hasn.plan.goal.add_key_result',
    'hasn.plan.project.list',
    'hasn.plan.project.get',
    'hasn.plan.project.create',
    'hasn.plan.project.update',
    'hasn.plan.project.delete',
    'hasn.plan.project.add_milestone',
    'hasn.plan.milestone.update',
    'hasn.plan.milestone.delete',
    'hasn.plan.todo.list',
    'hasn.plan.todo.get',
    'hasn.plan.todo.create',
    'hasn.plan.todo.update',
    'hasn.plan.todo.delete',
    'hasn.plan.event.list',
    'hasn.plan.event.create',
    'hasn.plan.event.update',
    'hasn.plan.event.delete',
    'hasn.plan.event.invite',
    'hasn.plan.event.rsvp',
    'hasn.plan.availability',
    'hasn.plan.habit.list',
    'hasn.plan.habit.create',
    'hasn.plan.habit.checkin',
    'hasn.plan.preference.get',
    'hasn.plan.preference.set',
}

# 留在本地 hasn-mcp 的（复合/引擎/daemon/纯本地）——绝不应迁到云端 platform tool。
_LOCAL_ONLY = {
    'hasn.plan.decompose',
    'hasn.plan.schedule',
    'hasn.plan.reschedule',
    'hasn.plan.briefing',
    'hasn.plan.review',
    'hasn.plan.delegate',
    'hasn.plan.validate',
}


def test_plan_tools_register_exactly_pure_relay() -> None:
    """34 个纯代理工具全注册（含 PLAN-ENT 企业会议协同 invite/rsvp/availability
    与 PLAN-LOOP 里程碑改删 milestone.update/delete），且不含任何应留本地的复合/引擎/daemon 工具。"""
    names = {t.name for t in PLAN_TOOLS}
    assert names == _EXPECTED_NAMES, f'差异: {names ^ _EXPECTED_NAMES}'
    assert not (names & _LOCAL_ONLY), '复合/引擎/daemon 工具不应迁到云端'


def test_plan_tools_are_cloud_platform() -> None:
    """全部 source=platform、namespace=hasn.plan、execution_location=cloud。"""
    for t in PLAN_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.plan'
        assert t.execution_location == 'cloud'


def test_plan_tools_scope_split_read_vs_write() -> None:
    """四类 scope（PLAN-ENT [04] §6 企业会议协同）：
    - 个人读类无 scope（出厂 Allow）；
    - 团队忙闲读 availability → plan:read（A3 可见性约束）；
    - 企业会议协同 invite/rsvp → plan:manage；
    - 其余写类 → plan:write。
    """
    reads = {
        'hasn.plan.today',
        'hasn.plan.goal.list',
        'hasn.plan.goal.get',
        'hasn.plan.project.list',
        'hasn.plan.project.get',
        'hasn.plan.todo.list',
        'hasn.plan.todo.get',
        'hasn.plan.event.list',
        'hasn.plan.habit.list',
        'hasn.plan.preference.get',
    }
    read_scoped = {'hasn.plan.availability'}  # 跨成员忙闲读，plan:read（受 A3 可见性约束）
    manage = {'hasn.plan.event.invite', 'hasn.plan.event.rsvp'}  # 企业会议协同，plan:manage
    for t in PLAN_TOOLS:
        if t.name in reads:
            assert t.required_scopes == [], f'{t.name} 个人读类不应有 scope'
        elif t.name in read_scoped:
            assert t.required_scopes == ['plan:read'], f'{t.name} 团队忙闲读应声明 plan:read'
        elif t.name in manage:
            assert t.required_scopes == ['plan:manage'], f'{t.name} 企业会议协同应声明 plan:manage'
        else:
            assert t.required_scopes == ['plan:write'], f'{t.name} 写类应声明 plan:write'


def test_priority_field_is_string_not_integer() -> None:
    """priority 必须声明 string（service 层归一化为 SMALLINT），否则 tool.call jsonschema 校验会拦掉 "high"。"""
    for name in ('hasn.plan.capture', 'hasn.plan.todo.create', 'hasn.plan.goal.create'):
        props = _tool(name).input_schema['properties']
        assert props['priority']['type'] == 'string', f'{name}.priority 应为 string'


def test_required_fields_match_contract() -> None:
    """关键必填项与原工具一致。"""
    assert _tool('hasn.plan.goal.create').input_schema['required'] == ['title']
    assert _tool('hasn.plan.today').input_schema['required'] == ['start', 'end']
    assert _tool('hasn.plan.goal.add_key_result').input_schema['required'] == ['goal_id', 'metric', 'target_value']
    assert _tool('hasn.plan.event.create').input_schema['required'] == ['title', 'start_at', 'end_at']
    # 读类不应有 required
    assert 'required' not in _tool('hasn.plan.todo.list').input_schema


def test_plan_scope_in_platform_catalog() -> None:
    """plan:write/plan:read/plan:manage 均登记平台 scope 展示目录（webui 能力管理可见可管控）。
    工具声明的每个 scope 都必须在目录中，否则 webui 无法展示/管控（PLAN-ENT 会议协同 scope）。"""
    from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

    for scope in ('plan:write', 'plan:read', 'plan:manage'):
        assert scope in PLATFORM_SCOPE_CATALOG, f'{scope} 未登记平台 scope 目录'
        assert PLATFORM_SCOPE_CATALOG[scope]['domain'] == 'plan'
    # 每个 plan 工具声明的 scope 都必须在目录中（防漂移）。
    declared = {s for t in PLAN_TOOLS for s in t.required_scopes}
    assert declared <= set(PLATFORM_SCOPE_CATALOG), (
        f'工具声明了未登记的 scope: {declared - set(PLATFORM_SCOPE_CATALOG)}'
    )


@pytest.mark.asyncio(loop_scope='module')
async def test_todo_create_requires_notes_for_agent_actor() -> None:
    """actor=agent/collab 的委托待办缺 notes → 返回 notes_required（不写库、不报 500）。"""
    res = await _tool('hasn.plan.todo.create').execute(
        _agent_ctx('h_no_such_owner_for_notes'), {'title': '委托任务', 'actor': 'agent'}
    )
    assert res.get('ok') is False
    assert res.get('error') == 'notes_required'


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_goal_and_todo_roundtrip_real_db() -> None:
    """真实 PG：建目标→读→改→列→删；capture→triage→读。事务真提交，测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete

    from backend.app.hasn_plan.model.goal import Goal
    from backend.app.hasn_plan.model.todo import Todo
    from backend.database.db import async_db_session

    owner = f'h_plan_tool_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner)
    try:
        # 1) 建目标（写类经 .begin() 真提交）
        goal = await _tool('hasn.plan.goal.create').execute(
            ctx, {'title': '学会游泳', 'category': '健康', 'priority': 'high'}
        )
        gid = goal['id']
        assert goal['title'] == '学会游泳'

        # 2) 读回（确认已落库、跨独立会话可见）
        got = await _tool('hasn.plan.goal.get').execute(ctx, {'id': gid})
        assert got['id'] == gid
        assert got['title'] == '学会游泳'

        # 3) 改
        upd = await _tool('hasn.plan.goal.update').execute(ctx, {'id': gid, 'title': '学会自由泳'})
        assert upd['title'] == '学会自由泳'

        # 4) 列表含它
        lst = await _tool('hasn.plan.goal.list').execute(ctx, {})
        assert any(g['id'] == gid for g in lst)

        # 5) capture（落收件箱：status=inbox/source=chat）→ triage 分诊
        cap = await _tool('hasn.plan.capture').execute(ctx, {'title': '买泳镜'})
        tid = cap['id']
        assert cap['status'] == 'inbox'
        assert cap['source'] == 'chat'

        tri = await _tool('hasn.plan.triage').execute(ctx, {'id': tid, 'status': 'todo', 'goal_id': gid})
        assert tri['status'] == 'todo'
        assert tri['goal_id'] == gid

        # 6) todo.create 带 priority 字符串（priority 归一化回归：不再 500，落库为 int）
        todo = await _tool('hasn.plan.todo.create').execute(ctx, {'title': '每周游 3 次', 'priority': 'high'})
        assert todo['priority'] == 3

        # 7) 删目标
        dele = await _tool('hasn.plan.goal.delete').execute(ctx, {'id': gid})
        assert dele == {'deleted': True}
    finally:
        async with async_db_session.begin() as db:
            await db.execute(delete(Todo).where(Todo.owner_hasn_id == owner))
            await db.execute(delete(Goal).where(Goal.owner_hasn_id == owner))
