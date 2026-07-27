"""多任务编排（工作流）N2 云端验收：manifest + scope + Agent API（真实 PG，零 mock）。

覆盖（实施 92 N2 验收）：
- manifest：hasn.workflow.* 能力齐全 + required_scopes/risk_level + 写类走统一授权开关
- scope catalog：workflow:read/manage/run 登记
- Agent JWT → /api/v1/hasn-task/agent/workflows：建菱形图 / 查图 / 列图 / 发现分身 / run / pause
- 定时图 → pending_approval + 主会话汇报卡；owner app approve → active
- 跨户 NotFound

事实源: docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §9；实施 92 N2。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_project.model import HasnProject
from backend.app.hasn_task.api.v1.agent.workflow import router as agent_workflow_router
from backend.app.hasn_task.api.v1.app.sync import router as app_sync_router
from backend.app.hasn_task.api.v1.app.workflow import router as app_workflow_router
from backend.app.hasn_task.api.v1.app.workflow_template import router as app_workflow_template_router
from backend.app.hasn_task.model.workflow_template import HasnWorkflowTemplate
from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception.errors import BaseExceptionError, ForbiddenError, RequestError
from backend.common.security.agent_jwt_auth import agent_jwt_auth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
from backend.database.redis import redis_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')
NODE_TABLES_SQL = (_SQL_DIR / '2026-07-14-workflow-node-tables.sql').read_text(encoding='utf-8')
ADVANCE_MODE_SQL = (_SQL_DIR / '2026-07-14-workflow-run-advance-mode.sql').read_text(encoding='utf-8')
WORKFLOW_HISTORY_SQL = (_SQL_DIR / '2026-07-26-workflow-history-recovery.sql').read_text(encoding='utf-8')

_APP = FastAPI()
_APP.include_router(agent_workflow_router, prefix='/api/v1/hasn-task/agent')
_APP.include_router(app_sync_router, prefix='/api/v1/hasn-task/app')
_APP.include_router(app_workflow_router, prefix='/api/v1/hasn-task/app')
_APP.include_router(app_workflow_template_router, prefix='/api/v1/hasn-task/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _uid() -> str:
    return uuid.uuid4().hex[:8]


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


async def _reset_redis_pool() -> None:
    """每例重连全局 Redis 池，避免连接绑定已关闭的 pytest 事件循环。"""
    try:
        await redis_client.connection_pool.disconnect()
    except Exception:
        pass


# ---------- 纯 Python：manifest + scope ----------


def test_manifest_has_workflow_capabilities() -> None:
    from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST

    caps = {c['mcp_name']: c for c in HASN_TASK_AI_NATIVE_MANIFEST['capabilities']}
    for name in (
        'hasn.workflow.create',
        'hasn.workflow.add_node',
        'hasn.workflow.add_edge',
        'hasn.workflow.list_agents',
        'hasn.workflow.get',
        'hasn.workflow.get_node_result',
        'hasn.workflow.run',
        'hasn.workflow.pause',
        'hasn.workflow.cancel',
        'hasn.workflow.list',
    ):
        assert name in caps, f'manifest 缺工作流能力 {name}'
    assert 'workflow:manage' in caps['hasn.workflow.create']['required_scopes']
    assert 'workflow:read' in caps['hasn.workflow.get']['required_scopes']
    assert 'workflow:run' in caps['hasn.workflow.run']['required_scopes']
    assert caps['hasn.workflow.cancel']['risk_level'] == 'high'
    # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可设 ask/deny override。
    assert caps['hasn.workflow.create']['human_confirmation']['required'] is False
    assert caps['hasn.workflow.list']['human_confirmation'] == {'required': False}


def test_scope_catalog_has_workflow() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert {'workflow:read', 'workflow:manage', 'workflow:run'} <= set(SCOPE_CATALOG)
    assert SCOPE_CATALOG['workflow:manage']['domain'] == 'task'


# ---------- Agent API E2E（真实 PG） ----------


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    await _reset_redis_pool()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    await _run_sql(AINATIVE_SQL)
    await _run_sql(WORKFLOW_SQL)
    await _run_sql(NODE_TABLES_SQL)
    await _run_sql(ADVANCE_MODE_SQL)
    await _run_sql(WORKFLOW_HISTORY_SQL)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = _uid()
    owner = f'h_wf_{tag}'
    other_owner = f'h_wf2_{tag}'
    # id 空间必须够宽：本项目「测试数据不删」是铁律，历次跑残留的 owner 行会持续累积。
    # 旧的 9000 槽位（970000~978999）撞唯一索引 idx_hasn_humans_star_id 的概率随跑的
    # 次数单调恶化 → fixture setup 抛 UniqueViolation → 该 loop 未清理 → 后续测试全部
    # 「got Future attached to a different loop / Event loop is closed」级联假红。
    # 放宽到千亿级槽位，碰撞概率可忽略（+1 给 other_owner 留位，仍在 bigint 内）。
    owner_uid = 900_000_000_000 + int(uuid.uuid4().int % 90_000_000_000)
    research = f'a_wf_r_{tag}'
    writer = f'a_wf_w_{tag}'

    session.add(
        HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='Owner', status='active')
    )
    session.add(
        HasnHumans(
            hasn_id=other_owner, star_id=f's_{owner_uid + 1}', user_id=owner_uid + 1, nickname='Other', status='active'
        )
    )
    session.add(
        HasnAgents(
            hasn_id=research, star_id=f'{_uid()}#star', owner_id=owner, display_name='研究分身', agent_name='research'
        )
    )
    session.add(
        HasnAgents(
            hasn_id=writer, star_id=f'{_uid()}#star', owner_id=owner, display_name='写作分身', agent_name='writer'
        )
    )
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    async def _agent_auth(request: Request) -> AgentTokenPayload:  # noqa: RUF029
        payload = AgentTokenPayload(
            agent_hasn_id=research,
            agent_name=f'agent_{tag}',
            owner_hasn_id=owner,
            owner_user_id=owner_uid,
            session_uuid=f'sess_{tag}',
            expire_time=datetime(2099, 1, 1, tzinfo=UTC),
        )
        request.state.agent = payload
        return payload

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=owner_uid, hasn_id=owner)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[agent_jwt_auth] = _agent_auth
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, owner=owner, other_owner=other_owner, research=research, writer=writer
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()
        await _reset_redis_pool()


def _data(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


def _diamond_body(
    e2e: SimpleNamespace, name: str, schedule_type: str = 'once', schedule_config: dict | None = None
) -> dict:
    return {
        'name': name,
        'goal': '调研并产出',
        'schedule_type': schedule_type,
        'schedule_config': schedule_config or {},
        'nodes': [
            {'node_key': 'plan', 'agent_id': e2e.research, 'prompt': '拆解'},
            {'node_key': 'cost', 'agent_id': e2e.research, 'prompt': '成本调研'},
            {'node_key': 'perf', 'agent_id': e2e.research, 'prompt': '性能调研'},
            {'node_key': 'synth', 'agent_id': e2e.writer, 'prompt': '综合'},
        ],
        'edges': [
            {'parent': 'plan', 'child': 'cost'},
            {'parent': 'plan', 'child': 'perf'},
            {'parent': 'cost', 'child': 'synth'},
            {'parent': 'perf', 'child': 'synth'},
        ],
    }


async def test_agent_create_get_list_workflow(e2e: SimpleNamespace) -> None:
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    wf = created['workflow']
    assert wf['workflow_id'].startswith('wf_')
    assert wf['status'] == 'active'  # 一次性图直接可跑
    assert wf['created_by_kind'] == 'agent'
    wfid = wf['workflow_id']

    detail = _data(await e2e.client.get(f'/api/v1/hasn-task/agent/workflows/{wfid}'))
    assert {n['node_key'] for n in detail['nodes']} == {'plan', 'cost', 'perf', 'synth'}
    assert len(detail['edges']) == 4

    listed = _data(await e2e.client.get('/api/v1/hasn-task/agent/workflows'))
    assert any(w['workflow_id'] == wfid for w in listed['workflows'])

    # 跨户 → NotFound
    resp = await e2e.client.get(f'/api/v1/hasn-task/agent/workflows/{wfid}')
    assert resp.status_code == 200  # 同户可见（sanity）


async def test_agent_list_agents(e2e: SimpleNamespace) -> None:
    data = _data(await e2e.client.get('/api/v1/hasn-task/agent/agents'))
    agent_ids = {a['agent_id'] for a in data['agents']}
    assert {e2e.research, e2e.writer} <= agent_ids


async def test_agent_run_and_pause(e2e: SimpleNamespace) -> None:
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    wfid = created['workflow']['workflow_id']

    paused = _data(await e2e.client.post(f'/api/v1/hasn-task/agent/workflows/{wfid}/pause'))
    assert paused['workflow']['status'] == 'paused'
    assert paused['workflow']['next_run_at'] is None

    ran = _data(await e2e.client.post(f'/api/v1/hasn-task/agent/workflows/{wfid}/run'))
    assert ran['workflow']['status'] == 'active'
    assert ran['workflow']['next_run_at'] is not None


async def test_agent_periodic_pending_approval_then_owner_approve(e2e: SimpleNamespace) -> None:
    body = _diamond_body(e2e, f'wf-cron-{_uid()}', schedule_type='interval', schedule_config={'minutes': 60})
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=body))
    wf = created['workflow']
    assert wf['status'] == 'pending_approval'  # D4：agent 建定时图待主人确认
    wfid = wf['workflow_id']

    # 自有分身向主人请示走主会话汇报卡，不污染通知中心。
    row = await e2e.session.execute(
        text(
            'SELECT content FROM hasn_messages '
            'WHERE to_id = :owner AND from_id = :agent AND content_type = 5 '
            'ORDER BY id DESC LIMIT 1'
        ),
        {'owner': e2e.owner, 'agent': e2e.research},
    )
    card = row.scalar_one()
    assert card['metadata']['report'] is True
    assert card['resource']['id'] == wfid

    # owner app approve → active
    approved = _data(await e2e.client.post(f'/api/v1/hasn-task/app/workflows/{wfid}/approve'))
    assert approved['workflow']['status'] == 'active'
    assert approved['workflow']['next_run_at'] is not None


async def test_create_rejects_cross_owner_node_agent(e2e: SimpleNamespace) -> None:
    foreign = f'a_foreign_{_uid()}'
    e2e.session.add(
        HasnAgents(
            hasn_id=foreign,
            star_id=f'{_uid()}#star',
            owner_id=e2e.other_owner,
            display_name='他人',
            agent_name='x',
        )
    )
    await e2e.session.flush()
    body = {
        'name': f'wf-bad-{_uid()}',
        'nodes': [{'node_key': 'a', 'agent_id': foreign, 'prompt': 'x'}],
        'edges': [],
    }
    resp = await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=body)
    assert resp.json()['code'] == 404, resp.text


async def test_owner_history_api_keeps_orphan_run_read_only(e2e: SimpleNamespace) -> None:
    """R3：父定义不在云端时，Owner 仍能通过只读 API 打开执行快照。"""
    workflow_run_uuid = f'wfr_history_{_uid()}'
    project_id = str(uuid.uuid4())
    synced = _data(
        await e2e.client.post(
            '/api/v1/hasn-task/app/workflow-node-runs:sync',
            json={
                'runs': [
                    {
                        'workflow_run_uuid': workflow_run_uuid,
                        'workflow_uuid': f'wf_missing_{_uid()}',
                        'workflow_name_snapshot': '归档场景',
                        'template_key_snapshot': 'one_person_company',
                        'project_id': project_id,
                        'status': 'failed',
                        'graph_snapshot': {
                            'nodes': [{'node_key': 'research'}, {'node_key': 'summary'}],
                            'edges': [['research', 'summary']],
                        },
                    }
                ],
                'node_runs': [
                    {
                        'node_run_uuid': f'ndr_history_{_uid()}',
                        'workflow_run_uuid': workflow_run_uuid,
                        'workflow_uuid': f'wf_unused_{_uid()}',
                        'node_key': 'research',
                        'status': 'done',
                        'output_summary': '调研完成',
                    },
                    {
                        'node_run_uuid': f'ndr_history_{_uid()}',
                        'workflow_run_uuid': workflow_run_uuid,
                        'workflow_uuid': f'wf_unused_{_uid()}',
                        'node_key': 'summary',
                        'status': 'failed',
                        'attention_reason': '需要人工补充资料',
                    },
                ],
            },
        )
    )
    assert synced == {'accepted_runs': 1, 'accepted_node_runs': 2, 'rejected': [], 'deferred': []}

    history = _data(await e2e.client.get('/api/v1/hasn-task/app/workflow-runs', params={'project_id': project_id}))
    item = next(row for row in history['items'] if row['workflow_run_id'] == workflow_run_uuid)
    assert item['workflow_name'] == '归档场景'
    assert item['definition_state'] == 'missing'
    assert item['progress'] == {'done': 1, 'total': 2}
    assert item['capabilities']['can_mutate'] is False

    detail = _data(await e2e.client.get(f'/api/v1/hasn-task/app/workflow-runs/{workflow_run_uuid}/scenario-view'))
    assert [node['node_key'] for node in detail['nodes']] == ['research', 'summary']
    assert detail['capabilities'] == {
        'can_mutate': False,
        'mutation_reason': 'remote_execution',
        'work_session_events': False,
    }
    assert detail['availability'] == {'work_session_events': 'unavailable_on_this_node'}


# ---------- P9-B 场景工作流项目轴实例化硬闸（doc95 §2.3） ----------


def _payload(e2e: SimpleNamespace) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=e2e.research,
        agent_name='wf-agent',
        owner_hasn_id=e2e.owner,
        owner_user_id=970001,
        session_uuid=f'sess_{_uid()}',
        expire_time=datetime(2099, 1, 1, tzinfo=UTC),
    )


async def _seed_template(session, key: str) -> HasnWorkflowTemplate:  # noqa: ANN001
    """种一条内置免费场景模板（is_builtin=True → 对任何 owner 可见，sku_ref=None → 免判权）。"""
    tpl = HasnWorkflowTemplate(
        template_key=key,
        template_uuid=f'wft_{key}',
        name='测试场景',
        is_builtin=True,
        status='active',
        graph_spec={
            'nodes': [
                {'node_key': 'origin', 'name': '起点', 'is_origin': True, 'prompt': '开始'},
                {'node_key': 'work', 'name': '干活', 'prompt': '产出'},
            ],
            'edges': [{'parent': 'origin', 'child': 'work'}],
        },
    )
    session.add(tpl)
    await session.flush()
    return tpl


async def _seed_project(session, owner: str, status: str = 'active') -> HasnProject:  # noqa: ANN001
    proj = HasnProject(owner_id=owner, name='测试项目', status=status)
    session.add(proj)
    await session.flush()
    return proj


async def test_instantiate_requires_project(e2e: SimpleNamespace) -> None:
    """无 project_id（显式无 + ContextVar 无）→ 结构化 PROJECT_REQUIRED。"""
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    with pytest.raises(McpToolError) as ei:
        await workflow_template_service.instantiate_template(
            e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={}
        )
    assert ei.value.code == McpErrorCode.PROJECT_REQUIRED


async def test_instantiate_with_project_lands_on_workflow(e2e: SimpleNamespace) -> None:
    """合法 project_id → 建图成功且 project_id 落到 workflow 行。"""
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    proj = await _seed_project(e2e.session, e2e.owner)
    result = await workflow_template_service.instantiate_template(
        e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(proj.id)}
    )
    assert result['project_id'] == str(proj.id)
    row = await e2e.session.execute(
        text('SELECT project_id FROM hasn_task.workflow WHERE workflow_uuid = :wu'),
        {'wu': result['workflow_id']},
    )
    assert str(row.scalar_one()) == str(proj.id)


async def test_instantiate_cross_owner_project_403(e2e: SimpleNamespace) -> None:
    """跨 owner 的 project_id → 403（不是 404，不做存在性隐藏）。"""
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    foreign = await _seed_project(e2e.session, e2e.other_owner)
    with pytest.raises(ForbiddenError):
        await workflow_template_service.instantiate_template(
            e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(foreign.id)}
        )


async def test_instantiate_archived_project_rejected(e2e: SimpleNamespace) -> None:
    """归档项目 → 结构化拒绝（error_code=project_archived）。"""
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    proj = await _seed_project(e2e.session, e2e.owner, status='archived')
    with pytest.raises(RequestError) as ei:
        await workflow_template_service.instantiate_template(
            e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(proj.id)}
        )
    assert (ei.value.data or {}).get('error_code') == 'PROJECT_ARCHIVED'


async def test_bare_workflow_create_unaffected(e2e: SimpleNamespace) -> None:
    """裸工程图创建路径（template_key IS NULL）不受硬闸影响，仍可建，project_id 为空。"""
    created = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    assert created['workflow']['project_id'] is None


# ---------- P9-D 项目侧聚合读：list 加 project_id 过滤（doc95 §4.3/§4.4） ----------


async def test_list_workflows_filters_by_project(e2e: SimpleNamespace) -> None:
    """给 project_id 只返该项目下的图；不给则全返（含项目外的裸图）。"""
    proj_a = await _seed_project(e2e.session, e2e.owner)
    proj_b = await _seed_project(e2e.session, e2e.owner)
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    wf_a = await workflow_template_service.instantiate_template(
        e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(proj_a.id)}
    )
    wf_b = await workflow_template_service.instantiate_template(
        e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(proj_b.id)}
    )
    bare = _data(await e2e.client.post('/api/v1/hasn-task/agent/workflows', json=_diamond_body(e2e, f'wf-{_uid()}')))
    bare_id = bare['workflow']['workflow_id']

    only_a = await agent_workflow_service.list_workflows(e2e.session, owner_id=e2e.owner, project_id=str(proj_a.id))
    ids_a = {w['workflow_id'] for w in only_a}
    assert wf_a['workflow_id'] in ids_a
    assert wf_b['workflow_id'] not in ids_a, '不该串到别的项目'
    assert bare_id not in ids_a, '项目外的裸图不该出现在项目过滤结果里'

    all_ids = {w['workflow_id'] for w in await agent_workflow_service.list_workflows(e2e.session, owner_id=e2e.owner)}
    assert {wf_a['workflow_id'], wf_b['workflow_id'], bare_id} <= all_ids, '不给 project_id 应全返'


async def test_list_workflows_project_filter_never_crosses_owner(e2e: SimpleNamespace) -> None:
    """项目只是过滤键、不是权限边界（doc95 §0.2 ①）：拿别人项目的 id 也不会看见别人的图。

    owner_id 才是隔离键，项目过滤永远叠在 owner 之上——两者是 AND，不是「在这个项目里所以能看见」。
    """
    foreign_proj = await _seed_project(e2e.session, e2e.other_owner)
    tpl = await _seed_template(e2e.session, f'tpl_{_uid()}')
    my_proj = await _seed_project(e2e.session, e2e.owner)
    mine = await workflow_template_service.instantiate_template(
        e2e.session, agent=_payload(e2e), template_key=tpl.template_key, params={'project_id': str(my_proj.id)}
    )

    # 用别人的 project_id 过滤自己的列表 → 空集（不报错、也不越权返对方的图）
    rows = await agent_workflow_service.list_workflows(e2e.session, owner_id=e2e.owner, project_id=str(foreign_proj.id))
    assert rows == []
    # 反向：对方拿我的 project_id 也看不到我的图
    foreign_rows = await agent_workflow_service.list_workflows(
        e2e.session, owner_id=e2e.other_owner, project_id=str(my_proj.id)
    )
    assert mine['workflow_id'] not in {w['workflow_id'] for w in foreign_rows}


async def test_owner_instantiate_is_idempotent_and_returns_definition_snapshot(e2e: SimpleNamespace) -> None:
    """R2：Owner 实例化先生成云端定义，网络重放不重复建图。"""
    template = await _seed_template(e2e.session, f'tpl_{_uid()}')
    project = await _seed_project(e2e.session, e2e.owner)
    idempotency_key = f'inst_{_uid()}'
    payload = {
        'project_id': str(project.id),
        'idempotency_key': idempotency_key,
        'goal': '先在云端建权威定义',
        'node_overrides': {
            'origin': {'agent_id': e2e.research},
            'work': {'agent_id': e2e.writer},
        },
    }

    first = _data(
        await e2e.client.post(
            f'/api/v1/hasn-task/app/workflow-templates/{template.template_key}:instantiate', json=payload
        )
    )
    replay = _data(
        await e2e.client.post(
            f'/api/v1/hasn-task/app/workflow-templates/{template.template_key}:instantiate', json=payload
        )
    )

    assert first['workflow_uuid'].startswith('wf_')
    assert replay['workflow_uuid'] == first['workflow_uuid']
    assert first['definition_revision'] == 1
    assert first['project_id'] == str(project.id)
    assert [node['node_key'] for node in first['graph_snapshot']['nodes']] == ['origin', 'work']
    count = await e2e.session.execute(
        text(
            'SELECT count(*) FROM hasn_task.workflow '
            'WHERE owner_id = :owner AND instantiation_idempotency_key = :idempotency_key'
        ),
        {'owner': e2e.owner, 'idempotency_key': idempotency_key},
    )
    assert count.scalar_one() == 1


async def test_legacy_definition_import_is_create_only_and_hash_idempotent(e2e: SimpleNamespace) -> None:
    """R2-c：旧 daemon 定义首次建图、同哈希重放不新增、差异图明确冲突。"""
    workflow_uuid = f'wf_legacy_{_uid()}'
    definition = {
        'workflow_uuid': workflow_uuid,
        'name': '旧版工程工作流',
        'goal': '把存量定义补到云端',
        'nodes': [
            {'node_key': 'origin', 'agent_id': e2e.research, 'prompt': '主人输入', 'is_origin': True},
            {'node_key': 'work', 'agent_id': e2e.writer, 'prompt': '完成执行'},
        ],
        'edges': [{'parent': 'origin', 'child': 'work'}],
    }
    payload = {'sync_protocol_version': 2, 'definitions': [{'workflow': definition}]}

    first = _data(await e2e.client.post('/api/v1/hasn-task/app/workflows:sync', json=payload))
    assert first == {'created': [workflow_uuid], 'idempotent': []}
    replay = _data(await e2e.client.post('/api/v1/hasn-task/app/workflows:sync', json=payload))
    assert replay == {'created': [], 'idempotent': [workflow_uuid]}

    conflicting = {
        **definition,
        'nodes': [
            {'node_key': 'origin', 'agent_id': e2e.research, 'prompt': '已被篡改', 'is_origin': True},
            {'node_key': 'work', 'agent_id': e2e.writer, 'prompt': '完成执行'},
        ],
    }
    response = await e2e.client.post(
        '/api/v1/hasn-task/app/workflows:sync',
        json={'sync_protocol_version': 2, 'definitions': [{'workflow': conflicting}]},
    )
    assert response.status_code == 409, response.text
    assert 'DEFINITION_CONFLICT' in response.text


async def test_sync_protocol_v2_defers_orphan_node_run_but_v1_keeps_compatibility(e2e: SimpleNamespace) -> None:
    """R2-c：新协议缺父 run 保持 pending，旧 daemon 协议仍可兼容写入历史。"""
    run_uuid = f'wfr_orphan_{_uid()}'
    node = {
        'node_run_uuid': f'ndr_orphan_{_uid()}',
        'workflow_run_uuid': run_uuid,
        'workflow_uuid': f'wf_orphan_{_uid()}',
        'node_key': 'legacy',
        'status': 'done',
    }
    deferred = _data(
        await e2e.client.post(
            '/api/v1/hasn-task/app/workflow-node-runs:sync',
            json={'sync_protocol_version': 2, 'node_runs': [node]},
        )
    )
    assert deferred['accepted_node_runs'] == 0
    assert deferred['rejected'] == []
    assert deferred['deferred'][0]['uuid'] == node['node_run_uuid']
    accepted = _data(
        await e2e.client.post(
            '/api/v1/hasn-task/app/workflow-node-runs:sync',
            json={'sync_protocol_version': 1, 'node_runs': [node]},
        )
    )
    assert accepted['accepted_node_runs'] == 1
    assert accepted['deferred'] == []
