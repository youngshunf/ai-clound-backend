"""内置工作流模板下发 loader 真实 PostgreSQL 测试（零 mock·P3-cloud-seed）。

覆盖（施工清单 doc94 §10-P3 收尾 · 场景=模板 doc11 §4.2 · hub 官方内置不变量）：
- 造临时 hub 仓目录（含 `workflow-templates/<slug>/workflow-template.yaml` 真 YAML）→ 调
  ``workflow_template_service.sync_builtin_workflow_templates(db, repo_root=...)`` → 断言 workflow_template 落行
- graph_spec JSONB 整块原样往返（嵌套 nodes/edges 完整）
- builtin_key 幂等：二次 sync 不重复插（按 template_key 唯一）、可更新派生字段（name/version/graph_spec…）
- 不覆盖 owner 非空的用户模板行（守 owner 归属边界）
- 单模板解析失败/字段缺失 → 记 warning 跳过，不中断整体（valid 仍落库）

事实源: docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化（场景即模板…）设计.md §4.2；施工 94 P3。
"""

from __future__ import annotations

import uuid

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
import yaml

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_task.crud.crud_workflow import hasn_workflow_template_dao
from backend.app.hasn_task.schema.workflow_template import CreateWorkflowTemplateParam
from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
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
    await _run_sql(TEMPLATE_SQL)
    await _run_sql(SOURCE_RELEASE_SQL)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield SimpleNamespace(session=session, engine=engine)
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


# ============================ 构造：临时 hub 仓 + 真 YAML ============================


def _graph_spec() -> dict[str, Any]:
    """迷你两节点链（起点 + 调研）——嵌套 output_spec/review_policy/display，验证 JSONB 整块往返。"""
    return {
        'nodes': [
            {
                'node_key': 'idea',
                'name': '想法立项',
                'node_kind': 'origin',
                'is_origin': True,
                'default_agent_type': None,
                'apps': [],
                'skills': [],
                'prompt': '',
                'system_prompt': '',
                # 起点是主人输入的内联锚点，预完成为 done、不过产出闸 → 不声明 output_spec（doc35 B3）。
                'review_policy': {'mode': 'none', 'max_rejects': 0},
                'display': {'order': 1, 'step_label': '想法'},
            },
            {
                'node_key': 'research',
                'name': '市场调研',
                'node_kind': 'agent',
                'is_origin': False,
                'default_agent_type': 'market_researcher',
                'apps': ['knowledge', 'growth'],
                'skills': [],
                'prompt': '围绕想法做市场研究',
                'system_prompt': '你是主人的市场研究专家',
                'output_spec': {
                    'required': True,
                    'label': '市场分析知识库',
                    'expects': [{'resource_kind': 'knowledge.base'}],
                },
                'review_policy': {'mode': 'none', 'max_rejects': 0},
                'display': {'order': 2, 'step_label': '调研'},
            },
        ],
        'edges': [{'parent': 'idea', 'child': 'research'}],
    }


def _wf_dict(
    *,
    template_key: str,
    template_uuid: str,
    name: str = '一人公司',
    domain: str = 'startup',
    sort_order: int = 1,
    version: int = 1,
    graph_spec: dict | None = None,
) -> dict[str, Any]:
    """一份对齐云端权威表的内置工作流模板声明（source=builtin·免费·owner 空）。"""
    return {
        'template_key': template_key,
        'template_uuid': template_uuid,
        'builtin_key': template_key,
        'is_builtin': True,
        'source': 'builtin',
        'domain': domain,
        'name': name,
        'tagline': '一个人跑通一家公司',
        'description': '从一个想法出发，走完调研、方案、获客，独自把它做成一门生意。',
        'icon': 'rocket',
        'accent': 'brand',
        'sort_order': sort_order,
        'status': 'active',
        'sku_ref': None,
        'market_ref': None,
        'owner_id': None,
        'version': version,
        'graph_spec': graph_spec if graph_spec is not None else _graph_spec(),
    }


def _write_hub(root: Path, slug: str, data: dict[str, Any]) -> None:
    """把声明写成真 YAML 到 `<root>/workflow-templates/<slug>/workflow-template.yaml`。"""
    d = root / 'workflow-templates' / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / 'workflow-template.yaml').write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8'
    )


# ============================ INSERT + graph_spec 往返 ============================


async def test_sync_inserts_builtin_row_with_graph_spec(env: SimpleNamespace, tmp_path: Path) -> None:
    tag = _uid()
    key = f'opc_{tag}'
    tuid = str(uuid.uuid4())
    _write_hub(tmp_path, 'one-person-company', _wf_dict(template_key=key, template_uuid=tuid))

    results = await workflow_template_service.sync_builtin_workflow_templates(
        env.session, repo_root=tmp_path
    )
    assert results['total'] == 1
    assert results['inserted'] == 1
    assert results['updated'] == 0 and results['skipped'] == 0 and results['failed'] == 0

    row = await hasn_workflow_template_dao.get_by_key(env.session, key)
    assert row is not None
    # 顶层标量映射
    assert row.template_uuid == tuid  # hub 声明的端云稳定 id 原样沿用
    assert row.is_builtin is True
    assert row.source == 'builtin'
    assert row.builtin_key == key
    assert row.owner_id is None
    assert row.domain == 'startup'
    assert row.status == 'active'
    assert row.accent == 'brand'
    # graph_spec JSONB 整块往返（嵌套结构完整）
    assert row.graph_spec['edges'] == [{'parent': 'idea', 'child': 'research'}]
    assert row.graph_spec['nodes'][1]['node_key'] == 'research'
    assert row.graph_spec['nodes'][1]['apps'] == ['knowledge', 'growth']
    assert row.graph_spec['nodes'][1]['output_spec']['expects'] == [{'resource_kind': 'knowledge.base'}]
    assert row.graph_spec['nodes'][0]['review_policy']['mode'] == 'none'


# ============================ 幂等 + builtin_key 可更新派生字段 ============================


async def test_sync_idempotent_and_updates_derived(env: SimpleNamespace, tmp_path: Path) -> None:
    tag = _uid()
    key = f'fin_{tag}'
    tuid = str(uuid.uuid4())
    _write_hub(
        tmp_path, 'fin-research',
        _wf_dict(template_key=key, template_uuid=tuid, name='金融投研', domain='finance', version=1),
    )

    first = await workflow_template_service.sync_builtin_workflow_templates(env.session, repo_root=tmp_path)
    assert first['inserted'] == 1

    # 改派生字段后二次下发：不重复插（按 template_key 唯一）、内置行可更新
    new_graph = _graph_spec()
    new_graph['nodes'][1]['name'] = '深度市场调研'
    _write_hub(
        tmp_path, 'fin-research',
        _wf_dict(
            template_key=key, template_uuid=tuid, name='金融投研（升级）',
            domain='finance', sort_order=5, version=2, graph_spec=new_graph,
        ),
    )
    second = await workflow_template_service.sync_builtin_workflow_templates(env.session, repo_root=tmp_path)
    assert second['inserted'] == 0
    assert second['updated'] == 1

    # 库中仍只有一行该 key（未重复插）
    cnt = await env.session.execute(
        sa.text('SELECT count(*) FROM hasn_task.workflow_template WHERE template_key = :k'),
        {'k': key},
    )
    assert cnt.scalar_one() == 1

    row = await hasn_workflow_template_dao.get_by_key(env.session, key)
    assert row is not None
    await env.session.refresh(row)
    assert row.name == '金融投研（升级）'  # 派生字段被覆盖
    assert row.sort_order == 5
    assert row.version == 2
    assert row.graph_spec['nodes'][1]['name'] == '深度市场调研'  # graph_spec 整块更新
    assert row.template_uuid == tuid  # 同步主键恒不动


# ============================ 不覆盖 owner 非空的用户模板行 ============================


async def test_sync_does_not_overwrite_owner_row(env: SimpleNamespace, tmp_path: Path) -> None:
    tag = _uid()
    key = f'user_{tag}'
    owner = f'owner_{tag}'

    # 先造一行用户自建模板（owner_id 非空·非内置）占用同一 template_key
    await workflow_template_service.create_template(
        env.session,
        owner_id=owner,
        obj=CreateWorkflowTemplateParam(
            template_key=key,
            name='用户自建模板',
            domain='finance',
            graph_spec={'nodes': [], 'edges': []},
            is_builtin=False,
            status='active',
            source='owner',
        ),
    )

    # hub 用同 key 下发内置声明 → 必须拒绝覆盖、跳过
    _write_hub(tmp_path, 'collide', _wf_dict(template_key=key, template_uuid=str(uuid.uuid4()), name='内置想改写'))
    results = await workflow_template_service.sync_builtin_workflow_templates(env.session, repo_root=tmp_path)
    assert results['skipped'] == 1
    assert results['inserted'] == 0 and results['updated'] == 0

    row = await hasn_workflow_template_dao.get_by_key(env.session, key)
    assert row is not None
    await env.session.refresh(row)
    assert row.owner_id == owner  # 归属未被抹掉
    assert row.name == '用户自建模板'  # 内容未被内置声明覆盖
    assert row.is_builtin is False


# ============================ 坏 YAML 跳过不中断整体 ============================


async def test_sync_skips_malformed_and_keeps_valid(env: SimpleNamespace, tmp_path: Path) -> None:
    tag = _uid()
    good_key = f'good_{tag}'
    _write_hub(tmp_path, 'good', _wf_dict(template_key=good_key, template_uuid=str(uuid.uuid4())))

    # 缺 graph_spec（字段缺失）
    _write_hub(tmp_path, 'no-graph', {'template_key': f'nog_{tag}', 'name': '缺图'})
    # 缺 template_key
    _write_hub(tmp_path, 'no-key', {'name': '无键', 'graph_spec': {'nodes': [], 'edges': []}})

    results = await workflow_template_service.sync_builtin_workflow_templates(env.session, repo_root=tmp_path)
    assert results['total'] == 3
    assert results['inserted'] == 1  # 仅 good 落库
    assert results['failed'] == 2  # 两个坏文件被跳过

    assert await hasn_workflow_template_dao.get_by_key(env.session, good_key) is not None


async def test_sync_missing_dir_returns_zero(env: SimpleNamespace, tmp_path: Path) -> None:
    # repo_root 下没有 workflow-templates/ 目录 → 记 warning、返回全零、不抛
    results = await workflow_template_service.sync_builtin_workflow_templates(env.session, repo_root=tmp_path)
    assert results == {'total': 0, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
