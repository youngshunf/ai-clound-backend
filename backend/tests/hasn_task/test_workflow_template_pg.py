"""工作流模板 P3-cloud 真实 PostgreSQL 测试（零 mock）。

覆盖（施工清单 doc94 §10-P3 / §11 验收）：
- 迁移幂等：``2026-07-14-workflow-template.sql`` 可重复执行；workflow_template 建表 + workflow 加 template_key
- 系统字典 workflow_template_domain seed：4 个领域（startup/finance/office/professional）可查
- 纯函数 derive_graph_summary：节点数 / 去重应用数 / 阶段面包屑按 order / 去重人设
- service：建模板（内置 / 主人自建）→ 列（可见性=内置+自己名下、domain 过滤、sort_order 排序、
  信封投影含 graph_summary）→ 取详情（graph_spec 往返）→ 跨户 NotFound
- template_key 全局唯一冲突拒绝

事实源: docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化（场景即模板…）设计.md §4.2；施工 94 P3。
"""

from __future__ import annotations

import uuid

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.schema.workflow_template import CreateWorkflowTemplateParam
from backend.app.hasn_task.service.workflow_template_service import (
    derive_graph_summary,
    workflow_template_service,
)
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
WORKFLOW_HISTORY_SQL = (_SQL_DIR / '2026-07-26-workflow-history-recovery.sql').read_text(encoding='utf-8')
TEMPLATE_SQL = (_SQL_DIR / '2026-07-14-workflow-template.sql').read_text(encoding='utf-8')
SOURCE_RELEASE_SQL = (_SQL_DIR / '2026-07-29-workflow-template-source-release.sql').read_text(encoding='utf-8')


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
    await _run_sql(WORKFLOW_HISTORY_SQL)
    await _run_sql(TEMPLATE_SQL)
    await _run_sql(SOURCE_RELEASE_SQL)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield SimpleNamespace(session=session, engine=engine)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _column_names(session, table: str) -> set[str]:
    rows = await session.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'hasn_task' AND table_name = :t"
        ),
        {'t': table},
    )
    return {r[0] for r in rows}


def _graph_spec() -> dict:
    """一人公司迷你链（起点 + 调研 + 方案）——足够验证图摘要派生。"""
    return {
        'nodes': [
            {'node_key': 'idea', 'name': '想法立项', 'is_origin': True, 'apps': [],
             'display': {'order': 1, 'step_label': '立项'}},
            {'node_key': 'research', 'name': '市场调研', 'default_agent_type': 'market_researcher',
             'apps': ['knowledge', 'growth'], 'display': {'order': 2, 'step_label': '调研'}},
            {'node_key': 'design', 'name': '方案设计', 'default_agent_type': 'product_strategist',
             'apps': ['knowledge'], 'display': {'order': 3, 'step_label': '方案'}},
        ],
        'edges': [{'from': 'idea', 'to': 'research'}, {'from': 'research', 'to': 'design'}],
    }


# ============================ 纯函数 ============================


def test_derive_graph_summary_pure() -> None:
    summary = derive_graph_summary(_graph_spec())
    assert summary.node_count == 3
    assert summary.app_count == 2  # knowledge ∪ growth 去重
    # apps 首见序去重：research 先带 knowledge、growth，design 再带 knowledge（不重复）
    assert summary.apps == ['knowledge', 'growth']
    assert [s['label'] for s in summary.steps] == ['立项', '调研', '方案']  # 按 order 排序
    assert summary.agent_types == ['market_researcher', 'product_strategist']  # 去重非空


def test_derive_graph_summary_empty() -> None:
    empty = derive_graph_summary(None)
    assert empty.node_count == 0 and empty.app_count == 0
    assert empty.apps == []
    assert empty.steps == [] and empty.agent_types == []


def test_template_to_workflow_params_maps_display() -> None:
    """模板节点的 display 必须透传进建图入参。

    doc35 B1 修死列时补了 output_spec/review_policy/apps/skills/is_origin，唯独漏了
    display——实例化后节点行 display 恒 {}，端侧链路图按 node_key 字母序兜底编号，
    「市场调研」被排成第 8 环。本测试钉死这层映射，防再次丢失。
    """
    tpl = SimpleNamespace(graph_spec=_graph_spec(), name='一人公司', description='链路详述')
    result = workflow_template_service._template_to_workflow_params(  # noqa: SLF001
        tpl, {}, default_agent_id='a_default'
    )
    nodes = {n['node_key']: n for n in result['nodes']}
    assert nodes['idea']['display'] == {'order': 1, 'step_label': '立项'}
    assert nodes['research']['display'] == {'order': 2, 'step_label': '调研'}
    assert nodes['design']['display'] == {'order': 3, 'step_label': '方案'}


# ============================ 迁移 + 字典 ============================


async def test_template_migration_idempotent_and_columns(env: SimpleNamespace) -> None:
    await _run_sql(TEMPLATE_SQL)  # 第二次执行：幂等

    rows = await env.session.execute(
        sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'hasn_task'")
    )
    assert 'workflow_template' in {r[0] for r in rows}

    cols = await _column_names(env.session, 'workflow_template')
    # doc11 §4.2 全字段（含审计补的 tagline/sort_order/source+market_ref/sku_ref）
    assert {
        'template_uuid', 'template_key', 'domain', 'name', 'tagline', 'description', 'sort_order',
        'icon', 'accent', 'graph_spec', 'is_builtin', 'builtin_key', 'status', 'owner_id',
        'source', 'market_ref', 'sku_ref', 'version',
    } <= cols
    # workflow 加溯源列 template_key（goal 已在基表）
    assert {'template_key', 'goal'} <= await _column_names(env.session, 'workflow')


async def test_domain_dict_seeded(env: SimpleNamespace) -> None:
    rows = await env.session.execute(
        sa.text(
            "SELECT value, label, color FROM sys_dict_data "
            "WHERE type_code = 'workflow_template_domain' ORDER BY sort"
        )
    )
    seeded = {r[0]: (r[1], r[2]) for r in rows.mappings().all()} if False else {
        m['value']: (m['label'], m['color']) for m in (await _domain_rows(env.session))
    }
    assert seeded['startup'] == ('个人创业', 'blue')
    assert seeded['finance'] == ('金融投研', 'teal')
    assert seeded['office'] == ('企业办公', 'indigo')
    assert seeded['professional'] == ('专业服务', 'rose')


async def _domain_rows(session):
    rows = await session.execute(
        sa.text(
            "SELECT value, label, color FROM sys_dict_data "
            "WHERE type_code = 'workflow_template_domain' ORDER BY sort"
        )
    )
    return rows.mappings().all()


# ============================ service：建 + 读 ============================


async def _mk(
    env: SimpleNamespace, *, key: str, owner_id: str | None, domain: str | None,
    status: str = 'active', sort_order: int = 0, is_builtin: bool = False, graph_spec: dict | None = None,
) -> None:
    obj = CreateWorkflowTemplateParam(
        template_key=key,
        name=f'模板-{key}',
        domain=domain,
        tagline='一句话',
        description='链路详述',
        sort_order=sort_order,
        icon='rocket',
        accent='brand',
        graph_spec=graph_spec if graph_spec is not None else _graph_spec(),
        is_builtin=is_builtin,
        status=status,
        source='builtin' if is_builtin else 'owner',
    )
    await workflow_template_service.create_template(env.session, owner_id=owner_id, obj=obj)


async def test_list_visibility_and_filters(env: SimpleNamespace) -> None:
    tag = _uid()
    owner_a, owner_b = f'oA_{tag}', f'oB_{tag}'
    k_builtin = f'builtin_{tag}'
    k_a = f'own_a_{tag}'
    k_b = f'own_b_{tag}'
    k_coming = f'coming_{tag}'
    k_plain = f'plain_{tag}'  # domain=None 普通工作流模板

    await _mk(env, key=k_builtin, owner_id=None, domain='startup', is_builtin=True, sort_order=1)
    await _mk(env, key=k_a, owner_id=owner_a, domain='finance', sort_order=2)
    await _mk(env, key=k_b, owner_id=owner_b, domain='office', sort_order=3)
    await _mk(env, key=k_coming, owner_id=None, domain='professional', is_builtin=True, status='coming_soon')
    await _mk(env, key=k_plain, owner_id=owner_a, domain=None, sort_order=4)

    # owner_a 视角：见内置 + 自己名下 + coming_soon 内置，不见 owner_b 的
    data = await workflow_template_service.list_templates(env.session, owner_id=owner_a)
    keys = {t['template_key'] for t in data['templates']}
    assert {k_builtin, k_a, k_coming, k_plain} <= keys
    assert k_b not in keys

    # 领域分组元数据随列表返回
    domains = {d['domain'] for d in data['domains']}
    assert {'startup', 'finance', 'office', 'professional'} <= domains

    # domain_only：只留 domain 非空的场景模板（k_plain 被过滤掉）
    scene = await workflow_template_service.list_templates(env.session, owner_id=owner_a, domain_only=True)
    scene_keys = {t['template_key'] for t in scene['templates']}
    assert {k_builtin, k_a, k_coming} <= scene_keys
    assert k_plain not in scene_keys

    # domain 精确过滤
    fin = await workflow_template_service.list_templates(env.session, owner_id=owner_a, domain='finance')
    fin_keys = {t['template_key'] for t in fin['templates']}
    assert k_a in fin_keys and k_builtin not in fin_keys

    # status 过滤
    active = await workflow_template_service.list_templates(env.session, owner_id=owner_a, status='active')
    active_keys = {t['template_key'] for t in active['templates']}
    assert k_builtin in active_keys and k_coming not in active_keys

    # sort_order 升序：我建的 key 之间 builtin(1) < a(2) < plain(4)
    ordered = [t['template_key'] for t in data['templates'] if t['template_key'] in {k_builtin, k_a, k_plain}]
    assert ordered == [k_builtin, k_a, k_plain]

    # 列表投影含 graph_summary，但不含 graph_spec（详情才带）
    a_row = next(t for t in data['templates'] if t['template_key'] == k_a)
    assert a_row['graph_summary']['node_count'] == 3
    assert a_row['graph_spec'] is None
    assert a_row['tagline'] == '一句话'


async def test_get_detail_roundtrip_and_cross_owner(env: SimpleNamespace) -> None:
    tag = _uid()
    owner_a, owner_b = f'gA_{tag}', f'gB_{tag}'
    k_builtin = f'gbuiltin_{tag}'
    k_a = f'gown_{tag}'

    await _mk(env, key=k_builtin, owner_id=None, domain='startup', is_builtin=True)
    await _mk(env, key=k_a, owner_id=owner_a, domain='finance')

    # 详情带 graph_spec 全量往返
    detail = await workflow_template_service.get_template(env.session, owner_id=owner_a, template_key=k_a)
    assert detail['graph_spec']['nodes'][1]['node_key'] == 'research'
    assert detail['graph_summary']['app_count'] == 2

    # 内置对任何人可见
    b_sees_builtin = await workflow_template_service.get_template(
        env.session, owner_id=owner_b, template_key=k_builtin
    )
    assert b_sees_builtin['is_builtin'] is True

    # 跨户私有模板 → NotFound（不泄露）
    with pytest.raises(errors.NotFoundError):
        await workflow_template_service.get_template(env.session, owner_id=owner_b, template_key=k_a)

    # 不存在 → NotFound
    with pytest.raises(errors.NotFoundError):
        await workflow_template_service.get_template(env.session, owner_id=owner_a, template_key=f'ghost_{tag}')


async def test_create_rejects_duplicate_key(env: SimpleNamespace) -> None:
    tag = _uid()
    key = f'dup_{tag}'
    await _mk(env, key=key, owner_id=f'o_{tag}', domain='startup')
    with pytest.raises(errors.RequestError, match='已存在'):
        await _mk(env, key=key, owner_id=f'o_{tag}', domain='finance')
    await env.session.rollback()
