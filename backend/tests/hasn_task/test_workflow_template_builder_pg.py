"""自定义场景搭建器 P3-cloud 真实 PostgreSQL 测试（零 mock，doc11 §4.5-2）。

覆盖（自定义场景全页搭建器的云端落点）：
- builder_options：返回权威 apps+resource_kinds / personas（3 内置）/ artifact_kinds（5 载体，无 resource）/ domains
- create_owner_template：过 §6.3 校验 → 存 source=owner + status draft/active；生成唯一 template_key；graph_spec 往返
- create 非法 status / 非法 graph_spec（空节点、边引用不存在）→ RequestError
- update_template 状态切换：draft ↔ active + version+1；非法 status 拒绝；改内置/跨户拒绝

事实源: docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化（场景即模板…）设计.md §4.5-2。
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
from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
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


def _valid_graph() -> dict:
    """一条最小合法链：起点（主人输入）→ 资料整理（落知识库）。边用 {parent, child}。"""
    return {
        'nodes': [
            {
                'node_key': 'brief', 'name': '需求输入', 'is_origin': True, 'apps': [],
                'display': {'order': 1, 'step_label': '输入'},
            },
            {
                'node_key': 'organize', 'name': '资料整理', 'default_agent_type': 'assistant',
                'apps': ['knowledge'], 'prompt': '把主人给的资料整理进知识库',
                'output_spec': {'expects': [{'resource_kind': 'knowledge.base'}], 'label': '整理后的知识库'},
                'display': {'order': 2, 'step_label': '整理'},
            },
        ],
        'edges': [{'parent': 'brief', 'child': 'organize'}],
    }


# ============================ builder_options ============================


async def test_builder_options(env: SimpleNamespace) -> None:
    opts = await workflow_template_service.builder_options(env.session)

    # personas：恰好 3 个内置人设，稳定序 + 展示名
    persona_keys = [p['key'] for p in opts['personas']]
    assert persona_keys == ['assistant', 'content_operator', 'analyst']
    labels = {p['key']: p['label'] for p in opts['personas']}
    assert labels['assistant'] == '全能助理'
    assert labels['content_operator'] == '创作专家'
    assert labels['analyst'] == '分析专家'

    # artifact_kinds：5 个载体维度，绝不含 resource（要判应用资源用 resource_kind）
    ak = {a['artifact_kind'] for a in opts['artifact_kinds']}
    assert ak == {'document', 'image', 'video', 'voice', 'file'}
    assert 'resource' not in ak

    # apps：非空，每项结构齐全；knowledge 应用声明了 knowledge.base
    apps = {a['app_id']: a for a in opts['apps']}
    assert 'knowledge' in apps
    kn = apps['knowledge']
    assert kn['name'] and 'icon' in kn
    kn_kinds = {rk['resource_kind'] for rk in kn['resource_kinds']}
    assert 'knowledge.base' in kn_kinds
    # 每个 resource_kind 前缀 = 所属 app_id（doc35 {app}.{kind}）
    for app_id, app in apps.items():
        for rk in app['resource_kinds']:
            assert rk['resource_kind'].split('.', 1)[0] == app_id

    # domains：随字典 seed（startup/finance/...）
    domain_values = {d['domain'] for d in opts['domains']}
    assert {'startup', 'finance'} <= domain_values


# ============================ create_owner_template ============================


async def test_create_owner_template_default_draft(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    result = await workflow_template_service.create_owner_template(
        env.session, owner_id=owner, params={'name': '我的场景', 'graph_spec': _valid_graph()}
    )
    assert result['source'] == 'owner'
    assert result['status'] == 'draft'  # 缺省草稿
    assert result['is_builtin'] is False
    assert result['owner_id'] == owner
    assert result['template_key'].startswith('tpl_')
    # graph_spec 全量往返（详情投影带 graph_spec）
    assert result['graph_spec']['nodes'][1]['node_key'] == 'organize'
    assert result['graph_summary']['node_count'] == 2
    assert result['graph_summary']['apps'] == ['knowledge']


async def test_create_owner_template_active(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    result = await workflow_template_service.create_owner_template(
        env.session, owner_id=owner,
        params={'name': '直接上架的场景', 'graph_spec': _valid_graph(), 'status': 'active', 'domain': 'startup'},
    )
    assert result['status'] == 'active'
    assert result['domain'] == 'startup'


async def test_create_owner_template_rejects_bad_status(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    with pytest.raises(errors.RequestError, match='status'):
        await workflow_template_service.create_owner_template(
            env.session, owner_id=owner,
            params={'name': 'x', 'graph_spec': _valid_graph(), 'status': 'archived'},
        )
    await env.session.rollback()


async def test_create_owner_template_rejects_empty_graph(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    with pytest.raises(errors.RequestError, match='至少需要一个节点'):
        await workflow_template_service.create_owner_template(
            env.session, owner_id=owner, params={'name': 'x', 'graph_spec': {'nodes': [], 'edges': []}}
        )
    await env.session.rollback()


async def test_create_owner_template_rejects_bad_edge(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    bad = _valid_graph()
    bad['edges'] = [{'parent': 'brief', 'child': 'ghost'}]  # child 不存在
    with pytest.raises(errors.RequestError, match='子节点不存在'):
        await workflow_template_service.create_owner_template(
            env.session, owner_id=owner, params={'name': 'x', 'graph_spec': bad}
        )
    await env.session.rollback()


# ============================ update_template 状态切换 ============================


async def test_update_template_status_and_version(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    created = await workflow_template_service.create_owner_template(
        env.session, owner_id=owner, params={'name': '草稿场景', 'graph_spec': _valid_graph()}
    )
    key = created['template_key']
    assert created['status'] == 'draft' and created['version'] == 1

    # 保存并上架：status draft → active + version+1
    updated = await workflow_template_service.update_template(
        env.session, owner_id=owner, template_key=key, params={'status': 'active', 'name': '改名后的场景'}
    )
    assert updated['status'] == 'active'
    assert updated['version'] == 2
    assert updated['name'] == '改名后的场景'


async def test_update_template_rejects_bad_status(env: SimpleNamespace) -> None:
    owner = f'o_{_uid()}'
    created = await workflow_template_service.create_owner_template(
        env.session, owner_id=owner, params={'name': '场景', 'graph_spec': _valid_graph()}
    )
    with pytest.raises(errors.RequestError, match='status'):
        await workflow_template_service.update_template(
            env.session, owner_id=owner, template_key=created['template_key'], params={'status': 'coming_soon'}
        )
    await env.session.rollback()


async def test_update_template_rejects_builtin(env: SimpleNamespace) -> None:
    tag = _uid()
    # 造一个内置行
    await workflow_template_service.create_template(
        env.session, owner_id=None,
        obj=CreateWorkflowTemplateParam(
            template_key=f'builtin_{tag}', name='内置', domain='startup', graph_spec=_valid_graph(),
            is_builtin=True, status='active', source='builtin',
        ),
    )
    with pytest.raises(errors.RequestError, match='内置'):
        await workflow_template_service.update_template(
            env.session, owner_id=f'o_{tag}', template_key=f'builtin_{tag}', params={'status': 'draft'}
        )
    await env.session.rollback()
