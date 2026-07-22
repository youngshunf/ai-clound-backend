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

from typing import Any

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.task import TASK_TOOLS


def _tool(name: str) -> Any:
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


def test_three_axis_in_schema_optional() -> None:
    """任务中心三轴均为可选，并禁止二进制入参。"""
    create_props = _tool('hasn.task.create').input_schema['properties']
    for k in ('project_id', 'app_id', 'execution_kind', 'execution_spec'):
        assert k in create_props, f'create 缺三轴入参 {k}'
    assert create_props['execution_kind']['enum'] == ['app_workflow', 'freeform']
    # 三轴均可选：不出现在 required 里
    required = set(_tool('hasn.task.create').input_schema['required'])
    assert not ({'project_id', 'app_id', 'execution_kind', 'execution_spec'} & required)
    list_props = _tool('hasn.task.list').input_schema['properties']
    assert set(list_props) == {'filter', 'limit'}
    filter_schema = list_props['filter']
    assert filter_schema['type'] == ['object', 'null']
    assert set(filter_schema['properties']) == {'project_id', 'app_id', 'agent', 'status'}
    update_props = _tool('hasn.task.update').input_schema['properties']
    assert {'project_id', 'app_id', 'execution_kind', 'execution_spec'} <= set(update_props)
    # 铁律：任务工具入参禁止二进制 base64
    for t in TASK_TOOLS:
        has_base64 = any(k.endswith('_base64') for k in t.input_schema.get('properties', {}))
        assert not has_base64, f'{t.name} 出现 base64 入参'


def test_task_tool_schema_matches_ai_native_manifest() -> None:
    """云端工具与 AI-Native manifest 的入参契约必须逐字段一致。"""
    from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST

    def contract_shape(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: contract_shape(item)
                for key, item in value.items()
                if key not in {'description', 'default', 'additionalProperties'}
            }
        if isinstance(value, list):
            return [contract_shape(item) for item in value]
        return value

    capabilities = {item['mcp_name']: item for item in HASN_TASK_AI_NATIVE_MANIFEST['capabilities']}
    for tool in TASK_TOOLS:
        manifest_schema = capabilities[tool.name]['input_schema']
        assert contract_shape(manifest_schema['properties']) == contract_shape(tool.input_schema['properties']), (
            f'{tool.name} properties 漂移'
        )
        assert manifest_schema.get('required', []) == tool.input_schema.get('required', []), (
            f'{tool.name} required 漂移'
        )


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


@pytest.mark.asyncio(loop_scope='module')
async def test_three_axis_roundtrip_and_filter_real_db() -> None:
    """真实 PG：三轴四列（doc12 §6.1）贯通事件溯源投影链。
    create 带三轴 → get/list 投影读回四轴 → execution_spec jsonb 深层往返不丢 →
    list 按 project_id/app_id 过滤命中/落空 → 暂停（task.updated）后三轴仍在（防 upsert 误清）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.app.hasn_project.service.project_app_service import project_service
    from backend.database.db import async_db_session

    owner = f'h_task_axis_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner, agent_hasn_id=f'a_axis_{uuid.uuid4().hex[:12]}')
    async with async_db_session.begin() as db:
        project = await project_service.create_project(db, owner=owner, data={'name': '任务三轴测试项目'})
    project_id = project['id']
    other_project_id = str(uuid.uuid4())
    app_id = 'hasn_growth'
    exec_spec = {'app_id': app_id, 'workflow_ref': 'wf_lead_nurture', 'params': {'batch': 3, 'tags': ['a', 'b']}}
    try:
        # 1) 建带三轴的 app_workflow 任务（execution_spec 含嵌套结构，测 jsonb 深层往返）
        created = await _tool('hasn.task.create').execute(
            ctx,
            {
                'name': '获客工作流巡检',
                'prompt': '推进线索培育',
                'schedule_type': 'once',
                'schedule_config': {'run_at': '2099-01-01T08:00:00+08:00'},
                'project_id': project_id,
                'app_id': app_id,
                'execution_kind': 'app_workflow',
                'execution_spec': exec_spec,
            },
        )
        tid = created['task_id']
        assert created['project_id'] == project_id, '创建投影 project_id 应回显'
        assert created['app_id'] == app_id
        assert created['execution_kind'] == 'app_workflow'
        assert created['execution_spec'] == exec_spec, 'execution_spec jsonb 深层往返不应丢'

        # 2) get 读回四轴（跨独立会话，证明真落库）
        got = await _tool('hasn.task.get').execute(ctx, {'task_id': tid})
        assert got['project_id'] == project_id
        assert got['app_id'] == app_id
        assert got['execution_kind'] == 'app_workflow'
        assert got['execution_spec'] == exec_spec

        # 3) list 投影也带四轴
        lst = await _tool('hasn.task.list').execute(ctx, {})
        mine = next(t for t in lst if t['task_id'] == tid)
        assert mine['project_id'] == project_id
        assert mine['execution_spec'] == exec_spec

        # 4) list 按 project_id 过滤：命中 / 落空
        by_proj = await _tool('hasn.task.list').execute(ctx, {'filter': {'project_id': project_id}})
        assert any(t['task_id'] == tid for t in by_proj), 'project_id 过滤应命中'
        by_other = await _tool('hasn.task.list').execute(ctx, {'filter': {'project_id': other_project_id}})
        assert not any(t['task_id'] == tid for t in by_other), '不同 project_id 应落空'

        # 5) list 按 app_id 过滤命中
        by_app = await _tool('hasn.task.list').execute(ctx, {'filter': {'app_id': app_id}})
        assert any(t['task_id'] == tid for t in by_app), 'app_id 过滤应命中'
        by_app_miss = await _tool('hasn.task.list').execute(ctx, {'filter': {'app_id': 'hasn_nonexistent'}})
        assert not any(t['task_id'] == tid for t in by_app_miss)
        by_agent_and_status = await _tool('hasn.task.list').execute(
            ctx, {'filter': {'agent': ctx.agent_hasn_id, 'status': 'scheduled'}}
        )
        assert any(t['task_id'] == tid for t in by_agent_and_status), 'agent/status 组合过滤应命中'

        # 6) 暂停（发 task.updated 事件，走 upsert ON CONFLICT）→ 三轴必须仍在（防 EXCLUDED 空值误清）
        paused = await _tool('hasn.task.pause').execute(ctx, {'task_id': tid})
        assert paused['state'] == 'paused'
        assert paused['project_id'] == project_id, (
            'task.updated 后 project_id 被误清 = _event_payload_from_row 未带三轴'
        )
        assert paused['app_id'] == app_id
        assert paused['execution_kind'] == 'app_workflow'
        assert paused['execution_spec'] == exec_spec

        # 7) 缺省三轴的 freeform 任务：project_id/app_id 为 None、execution_kind=freeform、execution_spec={}
        freeform = await _tool('hasn.task.create').execute(
            ctx,
            {
                'name': '自由巡检',
                'prompt': '看看有没有异常',
                'schedule_type': 'once',
                'schedule_config': {'run_at': '2099-01-02T08:00:00+08:00'},
            },
        )
        assert freeform['project_id'] is None
        assert freeform['app_id'] is None
        assert freeform['execution_kind'] == 'freeform'
        assert freeform['execution_spec'] == {}
    finally:
        async with async_db_session.begin() as db:
            await db.execute(text('DELETE FROM hasn_task.run_summary WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_task.task WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_project.hasn_project WHERE owner_id = :o'), {'o': owner})
