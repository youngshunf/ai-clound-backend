"""工作流模板 P5-cloud 分身工具 + §6.3 校验护栏 真实 PostgreSQL 测试（零 mock）。

覆盖（施工清单 doc94 §10-P5 / §11 验收）：
- §6.3 校验护栏 validate_graph_spec（纯函数）：合法图放行；有环 / 悬挂边 / 无起点 / 未知 app /
  未注册产物 kind / 节点超限 / node_key 重复 各自 raise 且 message 具体。
- draft：合法图 → 存 draft/source=agent/owner=本人/version=1 且画廊（list_templates owner）可见；非法图拒绝。
- update：version+1，只能改自己名下；改内置 / 改别人的 → 拒绝。
- get/list：owner 隔离 + 含 builtin。
- instantiate：据模板建出 cloud workflow（返 workflow_id），workflow.template_key 溯源落列；付费墙（P7·doc94
  §10-P7）：免费模板（sku_ref 空）放行，付费模板无权益抛 MCP_9219（data 携 AccessDecision）、有权益放行。
- publish：过校验 → status 转 active + version 快照 + market_ref 占位。
- 工具注册：6 工具 name/scope/schema + publish 出厂 ask（manifest human_confirmation.required=True）。

事实源: docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化…设计.md §4/§6.3；施工 94 P5。
"""

from __future__ import annotations

import uuid

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn_task.schema.workflow_template import CreateWorkflowTemplateParam
from backend.app.hasn_task.service.workflow_template_service import (
    _MAX_NODES,
    validate_graph_spec,
    workflow_template_service,
)
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_SQL_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'
AINATIVE_SQL = (_SQL_DIR / '2026-06-10-ainative-refactor.sql').read_text(encoding='utf-8')
WORKFLOW_SQL = (_SQL_DIR / '2026-06-11-workflow.sql').read_text(encoding='utf-8')
NODE_TABLES_SQL = (_SQL_DIR / '2026-07-14-workflow-node-tables.sql').read_text(encoding='utf-8')
ADVANCE_MODE_SQL = (_SQL_DIR / '2026-07-14-workflow-run-advance-mode.sql').read_text(encoding='utf-8')
TEMPLATE_SQL = (_SQL_DIR / '2026-07-14-workflow-template.sql').read_text(encoding='utf-8')


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
    await _run_sql(TEMPLATE_SQL)

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


def _valid_graph() -> dict:
    """迷你合法蓝图：起点 idea + agent 节点 research（apps/kind 都取真实注册值）。"""
    return {
        'nodes': [
            {
                'node_key': 'idea',
                'name': '想法立项',
                'node_kind': 'origin',
                'is_origin': True,
                'apps': [],
                'output_spec': {'kind': 'workflow_anchor', 'label': '想法'},
                'display': {'order': 1, 'step_label': '想法'},
            },
            {
                'node_key': 'research',
                'name': '市场调研',
                'node_kind': 'agent',
                'is_origin': False,
                'default_agent_type': 'assistant',
                'apps': ['knowledge', 'growth'],
                'output_spec': {'kind': 'knowledge_base', 'label': '调研'},
                'display': {'order': 2, 'step_label': '调研'},
            },
        ],
        'edges': [{'parent': 'idea', 'child': 'research'}],
    }


# ============================ §6.3 校验护栏（纯函数） ============================


def test_validate_graph_spec_valid() -> None:
    validate_graph_spec(_valid_graph())  # 不抛即通过


def test_validate_graph_spec_not_object() -> None:
    with pytest.raises(errors.RequestError, match='对象'):
        validate_graph_spec([])  # type: ignore[arg-type]


def test_validate_graph_spec_empty_nodes() -> None:
    with pytest.raises(errors.RequestError, match='至少需要一个节点'):
        validate_graph_spec({'nodes': [], 'edges': []})


def test_validate_graph_spec_rejects_cycle() -> None:
    graph = _valid_graph()
    graph['edges'].append({'parent': 'research', 'child': 'idea'})  # idea→research→idea 成环
    with pytest.raises(errors.RequestError, match='环'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_dangling_edge() -> None:
    graph = _valid_graph()
    graph['edges'].append({'parent': 'research', 'child': 'ghost'})
    with pytest.raises(errors.RequestError, match='ghost'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_no_origin() -> None:
    graph = _valid_graph()
    for node in graph['nodes']:
        node['is_origin'] = False
    with pytest.raises(errors.RequestError, match='起点'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_unknown_app() -> None:
    graph = _valid_graph()
    graph['nodes'][1]['apps'] = ['knowledge', 'nonexistent_app']
    with pytest.raises(errors.RequestError, match='nonexistent_app'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_unknown_output_kind() -> None:
    graph = _valid_graph()
    graph['nodes'][1]['output_spec'] = {'kind': 'bogus_kind', 'label': 'x'}
    with pytest.raises(errors.RequestError, match='未注册'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_duplicate_node_key() -> None:
    graph = _valid_graph()
    graph['nodes'][1]['node_key'] = 'idea'  # 与起点重键
    with pytest.raises(errors.RequestError, match='重复'):
        validate_graph_spec(graph)


def test_validate_graph_spec_rejects_over_max_nodes() -> None:
    nodes = [
        {'node_key': 'origin', 'is_origin': True, 'apps': [], 'output_spec': {'kind': 'workflow_anchor'}}
    ]
    nodes += [
        {'node_key': f'n{i}', 'is_origin': False, 'apps': [], 'output_spec': {'kind': 'document'}}
        for i in range(_MAX_NODES)  # 起点 + _MAX_NODES 个 = 超限
    ]
    with pytest.raises(errors.RequestError, match='超限'):
        validate_graph_spec({'nodes': nodes, 'edges': []})


def test_validate_graph_spec_allows_non_builtin_persona() -> None:
    """default_agent_type 非内置（需主人自备）也放行——软识别不拒绝。"""
    graph = _valid_graph()
    graph['nodes'][1]['default_agent_type'] = 'market_researcher'  # 非内置人设
    validate_graph_spec(graph)  # 不抛


# ============================ draft ============================


async def test_draft_valid_visible_in_gallery(env: SimpleNamespace) -> None:
    """P5 硬验收：draft 合法模板 → list_templates(owner) 能查到 status=draft 的它。"""
    owner = f'o_{_uid()}'
    drafted = await workflow_template_service.draft_template(
        env.session,
        owner_id=owner,
        params={'name': '一人公司', 'graph_spec': _valid_graph(), 'domain': 'startup', 'tagline': '一人跑通'},
    )
    assert drafted['status'] == 'draft'
    assert drafted['source'] == 'agent'
    assert drafted['owner_id'] == owner
    assert drafted['version'] == 1
    assert drafted['template_key'].startswith('tpl_')
    assert drafted['graph_spec'] is not None  # 详情带蓝图

    # 主人画廊可见该草稿
    data = await workflow_template_service.list_templates(env.session, owner_id=owner)
    hit = next((t for t in data['templates'] if t['template_key'] == drafted['template_key']), None)
    assert hit is not None
    assert hit['status'] == 'draft'
    await env.session.rollback()


async def test_draft_invalid_graph_rejected(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    bad = _valid_graph()
    bad['edges'].append({'parent': 'research', 'child': 'idea'})  # 成环
    with pytest.raises(errors.RequestError, match='环'):
        await workflow_template_service.draft_template(
            env.session, owner_id=owner, params={'name': '坏图', 'graph_spec': bad}
        )
    await env.session.rollback()


async def test_draft_owner_isolation(env: SimpleNamespace) -> None:
    """A draft 的私有草稿 B 不可见。"""
    owner_a, owner_b = f'oA_{_uid()}', f'oB_{_uid()}'
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner_a, params={'name': 'A的模板', 'graph_spec': _valid_graph()}
    )
    b_data = await workflow_template_service.list_templates(env.session, owner_id=owner_b)
    assert a['template_key'] not in {t['template_key'] for t in b_data['templates']}
    with pytest.raises(errors.NotFoundError):
        await workflow_template_service.get_template(env.session, owner_id=owner_b, template_key=a['template_key'])
    await env.session.rollback()


# ============================ update ============================


async def test_update_bumps_version_and_owner_only(env: SimpleNamespace) -> None:
    owner_a, owner_b = f'uA_{_uid()}', f'uB_{_uid()}'
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner_a, params={'name': '初版', 'graph_spec': _valid_graph()}
    )
    key = a['template_key']

    updated = await workflow_template_service.update_template(
        env.session, owner_id=owner_a, template_key=key, params={'name': '改名版', 'tagline': '新卖点'}
    )
    assert updated['version'] == 2
    assert updated['name'] == '改名版'
    assert updated['tagline'] == '新卖点'

    # 别人不能改（跨户不泄露 → NotFound）
    with pytest.raises(errors.NotFoundError):
        await workflow_template_service.update_template(
            env.session, owner_id=owner_b, template_key=key, params={'name': '越权'}
        )
    await env.session.rollback()


async def test_update_rejects_builtin(env: SimpleNamespace) -> None:
    """内置模板不可改（is_builtin/owner 空）。"""
    key = f'bi_{_uid()}'
    obj = CreateWorkflowTemplateParam(
        template_key=key, name='内置', graph_spec=_valid_graph(), is_builtin=True, status='active', source='builtin'
    )
    await workflow_template_service.create_template(env.session, owner_id=None, obj=obj)
    with pytest.raises(errors.RequestError, match='内置'):
        await workflow_template_service.update_template(
            env.session, owner_id=f'o_{_uid()}', template_key=key, params={'name': 'x'}
        )
    await env.session.rollback()


async def test_update_invalid_graph_rejected(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner, params={'name': '待改', 'graph_spec': _valid_graph()}
    )
    bad = _valid_graph()
    bad['nodes'][1]['apps'] = ['not_an_app']
    with pytest.raises(errors.RequestError, match='not_an_app'):
        await workflow_template_service.update_template(
            env.session, owner_id=owner, template_key=a['template_key'], params={'graph_spec': bad}
        )
    await env.session.rollback()


# ============================ get / list：owner 隔离 + builtin ============================


async def test_get_list_isolation_and_builtin(env: SimpleNamespace) -> None:
    owner_a, owner_b = f'gA_{_uid()}', f'gB_{_uid()}'
    # 内置模板
    bk = f'gbi_{_uid()}'
    await workflow_template_service.create_template(
        env.session,
        owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=bk, name='内置场景', graph_spec=_valid_graph(), is_builtin=True, status='active',
            source='builtin', domain='startup',
        ),
    )
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner_a, params={'name': 'A模板', 'graph_spec': _valid_graph()}
    )

    # A 看得到内置 + 自己名下
    a_keys = {t['template_key'] for t in (await workflow_template_service.list_templates(env.session, owner_id=owner_a))['templates']}
    assert {bk, a['template_key']} <= a_keys

    # B 看得到内置，看不到 A 的私有
    b_keys = {t['template_key'] for t in (await workflow_template_service.list_templates(env.session, owner_id=owner_b))['templates']}
    assert bk in b_keys
    assert a['template_key'] not in b_keys

    # 内置对任何人可 get
    got = await workflow_template_service.get_template(env.session, owner_id=owner_b, template_key=bk)
    assert got['is_builtin'] is True
    await env.session.rollback()


# ============================ instantiate ============================


async def test_instantiate_builds_cloud_workflow(env: SimpleNamespace) -> None:
    owner = f'io_{_uid()}'
    agent_id = f'ag_{_uid()}'
    await _seed_agent(env.session, owner_id=owner, agent_id=agent_id, name='发起分身')

    # 内置模板供实例化（免权益判定）
    key = f'ibi_{_uid()}'
    await workflow_template_service.create_template(
        env.session,
        owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=key, name='一人公司', graph_spec=_valid_graph(), is_builtin=True, status='active',
            source='builtin',
        ),
    )

    agent = AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='发起分身',
        owner_hasn_id=owner,
        owner_user_id=1,
        session_uuid=f'sess_{_uid()}',
        expire_time=datetime.now(),
    )
    result = await workflow_template_service.instantiate_template(
        env.session, agent=agent, template_key=key, params={'origin_input': '做一个 AI 记账 App', 'title': '我的一人公司'}
    )
    try:
        assert result['template_key'] == key
        wf_id = result['workflow_id']
        assert wf_id
        assert result['name'] == '我的一人公司'
        assert result['status'] == 'active'

        # template_key 溯源落 workflow 列
        tk = await env.session.execute(
            sa.text('SELECT template_key FROM hasn_task.workflow WHERE workflow_uuid = :wu'), {'wu': wf_id}
        )
        assert tk.scalar() == key

        # 两个节点都物化到 workflow_node（起点 idea + research），全指派发起分身
        wn = await env.session.execute(
            sa.text('SELECT node_key, agent_id FROM hasn_task.workflow_node WHERE workflow_uuid = :wu'), {'wu': wf_id}
        )
        rows = wn.mappings().all()
        assert {r['node_key'] for r in rows} == {'idea', 'research'}
        assert all(r['agent_id'] == agent_id for r in rows)
    finally:
        await env.session.rollback()


async def test_instantiate_cross_owner_template_not_found(env: SimpleNamespace) -> None:
    """别人的私有模板 → 实例化 NotFound（不泄露）。"""
    owner_a, owner_b = f'xa_{_uid()}', f'xb_{_uid()}'
    agent_b = f'agb_{_uid()}'
    await _seed_agent(env.session, owner_id=owner_b, agent_id=agent_b, name='B分身')
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner_a, params={'name': 'A私有', 'graph_spec': _valid_graph()}
    )
    agent = AgentTokenPayload(
        agent_hasn_id=agent_b, agent_name='B分身', owner_hasn_id=owner_b, owner_user_id=2,
        session_uuid=f'sess_{_uid()}', expire_time=datetime.now(),
    )
    with pytest.raises(errors.NotFoundError):
        await workflow_template_service.instantiate_template(
            env.session, agent=agent, template_key=a['template_key'], params={}
        )
    await env.session.rollback()


# ============================ instantiate 付费墙（P7-cloud） ============================


async def _seed_paid_offering(session: AsyncSession, *, offering_key: str, feature_key: str) -> None:
    """种一条 active offering + active plan（无试用 → 无权益即 need_purchase）挂到 feature_key。"""
    session.add(
        BillingOffering(
            key=offering_key,
            kind='feature_plan',
            feature_key=feature_key,
            display_name='付费场景模板',
            status='active',
            source='platform',
        )
    )
    session.add(
        BillingPlan(
            offering_key=offering_key,
            plan_key='standard',
            price_amount=Decimal(29),
            price_unit='cny',
            cycle='month',
            quota_json={},
            trial_json={},  # 不开试用 → 无权益判 need_purchase（非 trial_available）
            grace_json={},
            status='active',
        )
    )
    await session.flush()


async def _mk_paid_builtin_template(session: AsyncSession, *, key: str, sku_ref: str) -> None:
    """建一条 builtin 付费模板（owner_id=None 全员可见，sku_ref 非空触发真判权）。"""
    await workflow_template_service.create_template(
        session,
        owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=key,
            name='付费一人公司',
            graph_spec=_valid_graph(),
            is_builtin=True,
            status='active',
            source='builtin',
            sku_ref=sku_ref,
        ),
    )


async def test_instantiate_paid_template_denied_without_entitlement(env: SimpleNamespace) -> None:
    """付费模板（sku_ref 非空）+ 主人无权益 → 结构化拒绝 MCP_9219，data 携 AccessDecision(need_purchase)。"""
    tag = _uid()
    owner = f'paidO_{tag}'
    agent_id = f'paidAg_{tag}'
    key = f'paidTpl_{tag}'
    okey = f'off_wft_{tag}'
    feature_key = f'workflow_template:{key}'
    await _seed_agent(env.session, owner_id=owner, agent_id=agent_id, name='发起分身')
    await _seed_paid_offering(env.session, offering_key=okey, feature_key=feature_key)
    await _mk_paid_builtin_template(env.session, key=key, sku_ref=okey)

    agent = AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='发起分身',
        owner_hasn_id=owner,
        owner_user_id=1,
        session_uuid=f'sess_{tag}',
        expire_time=datetime.now(),
    )
    with pytest.raises(McpToolError) as ei:
        await workflow_template_service.instantiate_template(
            env.session, agent=agent, template_key=key, params={'origin_input': '想法'}
        )
    err = ei.value
    assert err.code is McpErrorCode.WORKFLOW_TEMPLATE_ENTITLEMENT_REQUIRED
    # data 携完整 AccessDecision（供 daemon→webui PaywallDialog 渲染）
    decision = err.data['decision']
    assert decision['allowed'] is False
    assert decision['reason'] == 'need_purchase'
    assert decision['feature_key'] == feature_key
    assert decision['offer']['offering_key'] == okey  # offer 指向可购商品
    await env.session.rollback()


async def test_instantiate_paid_template_allowed_with_entitlement(env: SimpleNamespace) -> None:
    """付费模板 + 主人已 grant 有效权益 → 放行，正常建出 cloud workflow（返 workflow_id）。"""
    tag = _uid()
    owner = f'entO_{tag}'
    agent_id = f'entAg_{tag}'
    key = f'entTpl_{tag}'
    okey = f'off_wft_{tag}'
    feature_key = f'workflow_template:{key}'
    await _seed_agent(env.session, owner_id=owner, agent_id=agent_id, name='发起分身')
    await _seed_paid_offering(env.session, offering_key=okey, feature_key=feature_key)
    await _mk_paid_builtin_template(env.session, key=key, sku_ref=okey)

    # grant 主人有效权益（active、expires_at 空=永久买断）
    env.session.add(
        HasnAppEntitlement(
            app_id=feature_key,  # 通用特征无 catalog app_id，用 feature_key 占位保唯一
            feature_key=feature_key,
            subject_type='owner',
            subject_id=owner,
            source='purchase',
            status='active',
            quota_json={},
        )
    )
    await env.session.flush()

    agent = AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='发起分身',
        owner_hasn_id=owner,
        owner_user_id=1,
        session_uuid=f'sess_{tag}',
        expire_time=datetime.now(),
    )
    result = await workflow_template_service.instantiate_template(
        env.session, agent=agent, template_key=key, params={'title': '已购一人公司'}
    )
    try:
        assert result['template_key'] == key
        assert result['workflow_id']
        assert result['name'] == '已购一人公司'
    finally:
        await env.session.rollback()


async def test_instantiate_free_template_bypasses_paywall(env: SimpleNamespace) -> None:
    """免费模板（sku_ref=None）直接放行，不进判权（即便 owner 无任何权益）。"""
    tag = _uid()
    owner = f'freeO_{tag}'
    agent_id = f'freeAg_{tag}'
    key = f'freeTpl_{tag}'
    await _seed_agent(env.session, owner_id=owner, agent_id=agent_id, name='发起分身')
    # sku_ref 缺省=None（免费）
    await workflow_template_service.create_template(
        env.session,
        owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=key,
            name='免费一人公司',
            graph_spec=_valid_graph(),
            is_builtin=True,
            status='active',
            source='builtin',
        ),
    )
    agent = AgentTokenPayload(
        agent_hasn_id=agent_id,
        agent_name='发起分身',
        owner_hasn_id=owner,
        owner_user_id=1,
        session_uuid=f'sess_{tag}',
        expire_time=datetime.now(),
    )
    result = await workflow_template_service.instantiate_template(
        env.session, agent=agent, template_key=key, params={}
    )
    try:
        assert result['workflow_id']
        assert result['template_key'] == key
    finally:
        await env.session.rollback()


# ============================ publish ============================


async def test_publish_transitions_status_and_snapshot(env: SimpleNamespace) -> None:
    owner = f'po_{_uid()}'
    a = await workflow_template_service.draft_template(
        env.session, owner_id=owner, params={'name': '待上架', 'graph_spec': _valid_graph()}
    )
    key = a['template_key']
    assert a['status'] == 'draft'

    published = await workflow_template_service.publish_template(env.session, owner_id=owner, template_key=key)
    assert published['status'] == 'active'
    assert published['version'] == 2  # version 快照升版
    assert published['market_ref'] == f'{key}@2'  # 市场溯源占位
    await env.session.rollback()


async def test_publish_rejects_builtin(env: SimpleNamespace) -> None:
    key = f'pbi_{_uid()}'
    await workflow_template_service.create_template(
        env.session,
        owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=key, name='内置', graph_spec=_valid_graph(), is_builtin=True, status='active', source='builtin'
        ),
    )
    with pytest.raises(errors.RequestError, match='内置'):
        await workflow_template_service.publish_template(env.session, owner_id=f'o_{_uid()}', template_key=key)
    await env.session.rollback()


# ============================ 工具注册（纯 Python，无 DB） ============================


def test_template_tools_names_scopes_schemas() -> None:
    from backend.app.mcp.tools.workflow import WORKFLOW_TEMPLATE_TOOLS

    by_name = {t.name: t for t in WORKFLOW_TEMPLATE_TOOLS}
    assert set(by_name) == {
        'hasn.workflow.template.draft',
        'hasn.workflow.template.update',
        'hasn.workflow.template.get',
        'hasn.workflow.template.list',
        'hasn.workflow.template.instantiate',
        'hasn.workflow.template.publish',
    }
    for t in WORKFLOW_TEMPLATE_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.workflow'
        assert t.execution_location == 'cloud'

    # scope：读无 scope / 建管 workflow:manage / 实例化 workflow:run
    assert by_name['hasn.workflow.template.get'].required_scopes == []
    assert by_name['hasn.workflow.template.list'].required_scopes == []
    assert by_name['hasn.workflow.template.draft'].required_scopes == ['workflow:manage']
    assert by_name['hasn.workflow.template.update'].required_scopes == ['workflow:manage']
    assert by_name['hasn.workflow.template.publish'].required_scopes == ['workflow:manage']
    assert by_name['hasn.workflow.template.instantiate'].required_scopes == ['workflow:run']

    # required 字段
    assert by_name['hasn.workflow.template.draft'].input_schema['required'] == ['name', 'graph_spec']
    assert by_name['hasn.workflow.template.instantiate'].input_schema['required'] == ['template_key']
    assert 'required' not in by_name['hasn.workflow.template.list'].input_schema


def test_manifest_template_capabilities_and_publish_ask() -> None:
    from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST

    caps = {c['mcp_name']: c for c in HASN_TASK_AI_NATIVE_MANIFEST['capabilities']}
    for name in (
        'hasn.workflow.template.draft',
        'hasn.workflow.template.update',
        'hasn.workflow.template.get',
        'hasn.workflow.template.list',
        'hasn.workflow.template.instantiate',
        'hasn.workflow.template.publish',
    ):
        assert name in caps, f'manifest 缺模板能力 {name}'

    # 出厂多为 allow；唯 publish 出厂 ask（外发+动钱）
    assert caps['hasn.workflow.template.draft']['human_confirmation']['required'] is False
    assert caps['hasn.workflow.template.instantiate']['human_confirmation']['required'] is False
    assert caps['hasn.workflow.template.publish']['human_confirmation']['required'] is True
    assert caps['hasn.workflow.template.publish']['risk_level'] == 'high'
    assert caps['hasn.workflow.template.instantiate']['required_scopes'] == ['workflow:run']
