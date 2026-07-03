"""OP-P3-B/C — design（矢量设计应用，源自 OpenPencil，模块 14 doc27）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端注册（catalog/manifest 注册 + 铸 scope + 轻登记表 hasn_design_project，实施 27 P3-B/C）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（19 个；读类 6 → design:read、写类 10 创作 + 2 破坏性 → design:write、
  codegen 1 → design:codegen；mcp_name 全 hasn.design.*）。
- scopes.py 登记 design:read/:write/:codegen（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- scope 授权走三态 capability_modes（JWT scopes claim 已退役，实施102 S0）：design:read/:write/:codegen
  由 ``hasn_agent_scopes.{default_mode, capability_modes}`` 消费时活取判定，凭证不再承载 scope。
- 跨仓零漂移：manifest 管理类（非 :read）required_scopes 集合 == {design:write, design:codegen}
  （= hasn-node crates/hasn-mcp/src/design.rs capability_scopes() 契约，OP-P3-A 待落，本测为云端侧契约源）。
- App 形态（local_tool / 手动安装 / 项目管理+派发台 /apps/design）。
- catalog 出厂源：sort_order 78 / default_agent_type(designer) / config_json（engine 分发骨架 bundled_deps=['node']）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等；
  ``ensure_catalog_seeded`` 幂等播种 design catalog 行（重复跑不重复插）+ config_json 经 app_configs 下发；
  轻登记表 ``hasn_design_project`` 落 ``hasn_design`` schema，CRUD create→query→delete + owner 行级隔离往返。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/27-OpenPencil矢量设计工具接入设计(本地sidecar·画布即应用).md
        §5.3/§5.4/§5.9；同目录 实施/27-OpenPencil矢量设计工具接入实施清单.md P3-B/C。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn.service.app_catalog_service import (
    _CATALOG_AGENT_DEFAULTS,
    _CATALOG_DEFAULT_CONFIG,
    _CATALOG_SORT_ORDER,
    ensure_catalog_seeded,
    get_all_app_configs,
    get_catalog,
)
from backend.app.hasn_design.crud.crud_hasn_design_project import hasn_design_project_dao
from backend.app.hasn_design.manifest import DESIGN_AI_NATIVE_MANIFEST, build_design_app
from backend.app.hasn_design.model import HasnDesignProject
from backend.app.hasn_design.model._base import APP_SCHEMA
from backend.app.hasn_design.schema.hasn_design_project import (
    CreateHasnDesignProjectParam,
)
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 落地真相（hasn-node crates/hasn-mcp/src/design.rs，OP-P3-A 待落，本表是云端侧契约源）：
# 读类 6 / 写类 10 创作 + 2 破坏性 / codegen 1 = 19。
_READ_TOOLS = {'get', 'get_selection', 'read_nodes', 'find_empty_space', 'get_design_prompt', 'export'}
_WRITE_TOOLS = {
    'batch_design',
    'skeleton',
    'content',
    'refine',
    'insert_node',
    'update_node',
    'move_node',
    'copy_node',
    'set_variables',
    'set_themes',
    'delete_node',
    'replace_node',
}
_CODEGEN_TOOLS = {'codegen'}
# 破坏性写类出厂 Ask（human_confirmation.required=True）；其余创作写类出厂 Allow。
_ASK_TOOLS = {'delete_node', 'replace_node'}
_READ_SCOPE = 'design:read'
_WRITE_SCOPE = 'design:write'
_CODEGEN_SCOPE = 'design:codegen'


# ============================ 纯 Python（无 DB）============================


def test_design_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(DESIGN_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_design_in_builtin_registry() -> None:
    """design 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'design' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('design')
    assert manifest['app_id'] == 'design'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    assert manifest['transport_mode'] == 'local'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_design_capabilities_match_landed_tools() -> None:
    """19 个 capability，mcp_name 全 hasn.design.*；读类 design:read、写类 design:write、codegen design:codegen。"""
    caps = DESIGN_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _READ_TOOLS | _WRITE_TOOLS | _CODEGEN_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 19

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.design.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _READ_TOOLS:
            assert cap['required_scopes'] == [_READ_SCOPE], f'{short} 读类应为 design:read'
            assert cap['human_confirmation'].get('required') is False
        elif short in _CODEGEN_TOOLS:
            assert cap['required_scopes'] == [_CODEGEN_SCOPE], f'{short} 应为 design:codegen'
            assert cap['human_confirmation'].get('required') is False
        else:
            assert cap['required_scopes'] == [_WRITE_SCOPE], f'{short} 写类应为 design:write'
            # 创作类出厂 Allow，破坏性 delete/replace 出厂 Ask（§5.3 note）。
            assert cap['human_confirmation'].get('required') is (short in _ASK_TOOLS)


def test_design_management_scopes_match_cross_repo_contract() -> None:
    """管理类（非 :read）required_scopes 集合 == {design:write, design:codegen}（= design.rs 契约）。"""
    management = {
        scope
        for cap in DESIGN_AI_NATIVE_MANIFEST['capabilities']
        for scope in (cap.get('required_scopes') or [])
        if not scope.endswith(':read')
    }
    assert management == {_WRITE_SCOPE, _CODEGEN_SCOPE}


def test_design_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 design:read/:write/:codegen（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _CODEGEN_SCOPE):
        assert scope in SCOPE_CATALOG
        assert scope_meta(scope)['domain'] == 'design'
    assert scope_meta(_READ_SCOPE)['label'] == '查看设计画布'
    assert scope_meta(_WRITE_SCOPE)['label'] == '在画布上出设计'
    assert scope_meta(_CODEGEN_SCOPE)['label'] == '设计稿出代码'


def test_design_scope_factory_defaults() -> None:
    """出厂默认：design:read/:write/:codegen 均出厂 Allow（创作类画布迭代不花算力，破坏性单 capability 走 Ask）。

    design:write 在 scope 层出厂 allow（创作迭代放行，对齐 studio:write 哲学）；破坏性 delete/replace 在
    capability human_confirmation=True 走 Ask（per-capability 真相，scope 层 default_mode 仅展示）。
    """
    assert scope_meta(_READ_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_WRITE_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_CODEGEN_SCOPE)['default_mode'] == 'allow'


def test_design_notifications_emit_declared() -> None:
    """design 声明 notifications.emit（派发即发『打开编辑器看 TA 设计』卡，§5.9(5)）。"""
    emit = DESIGN_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '矢量设计'


def test_design_ui_interface_standalone_window() -> None:
    """UI 载体：独立窗口（standalone）经 daemon 反代加载 OpenPencil 真画布（§5.5 福仔拍板，非主窗口 iframe）。"""
    ui = DESIGN_AI_NATIVE_MANIFEST['ui_interfaces']
    assert ui == [{'face': 'ui', 'transport': 'daemon_direct', 'window': 'standalone'}]


def test_design_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（本地 sidecar 按需装）+ 项目管理+派发台 /apps/design（非自动挂载）。"""
    app = build_design_app()
    assert app.id == 'design'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/design'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(DESIGN_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert DESIGN_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_design_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order 78 / designer 默认承接 / config_json engine 分发骨架。"""
    assert _CATALOG_SORT_ORDER['design'] == 78
    assert _CATALOG_AGENT_DEFAULTS['design'][0] == 'designer'
    assert _CATALOG_AGENT_DEFAULTS['design'][1]  # 业务提示词非空

    cfg = _CATALOG_DEFAULT_CONFIG['design']
    # 本地 sidecar：只承载 engine 分发骨架（Node 服务，bundled_deps=['node']），无独立模型配置（生成走分身自己的 LLM）。
    assert cfg['engine']['bundled_deps'] == ['node']
    assert not cfg['engine']['version']
    assert cfg['engine']['packages'] == {}
    assert 'models' not in cfg


# ============================ 真实 PostgreSQL ============================


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()  # 会话内变更回滚，不污染 dev DB
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_design_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 design manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'design')
    assert published['app_id'] == 'design'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(DESIGN_AI_NATIVE_MANIFEST)

    again = await ai_native_app_registry.ensure_builtin_published(db, 'design')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_design_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 design catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='design')
    assert cat is not None, 'design catalog 行未播种'
    assert cat.app_id == 'design'
    assert cat.name == '矢量设计'
    assert cat.execution_mode == 'local_tool'
    assert cat.entry_route == '/apps/design'
    assert cat.default_agent_type == 'designer'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'
    assert cat.config_json['engine']['bundled_deps'] == ['node']

    # 二次播种：不重复插（design 行仍唯一）。
    await ensure_catalog_seeded(db)
    cnt = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'design')
        )
    ).scalar()
    assert cnt == 1, f'design catalog 行应唯一，实际 {cnt}'


@pytest.mark.asyncio
async def test_design_config_in_app_configs_downlink(db: AsyncSession) -> None:
    """app_configs.design 经 get_all_app_configs 聚合下发（daemon 据此读引擎分发骨架注入 sidecar）。"""
    await ensure_catalog_seeded(db)
    app_configs = await get_all_app_configs(db)
    assert 'design' in app_configs, 'design 未进 app_configs 下发聚合'
    assert app_configs['design']['engine']['bundled_deps'] == ['node']


def test_design_project_table_in_app_schema() -> None:
    """轻登记表 hasn_design_project 落独立 schema hasn_design（不在 public，ADR-15）。"""
    assert APP_SCHEMA == 'hasn_design'
    assert HasnDesignProject.__table__.schema == 'hasn_design'
    assert HasnDesignProject.__tablename__ == 'hasn_design_project'


@pytest.mark.asyncio
async def test_design_project_crud_roundtrip_with_owner_isolation(db: AsyncSession) -> None:
    """轻登记表真 PG 往返：建项目（两 owner）→ 按 owner_hasn_id 查（行级隔离）→ 删（会话末回滚不污染 dev DB）。"""
    owner_a = f'h_design_a_{uuid.uuid4().hex[:8]}'
    owner_b = f'h_design_b_{uuid.uuid4().hex[:8]}'

    # 建：owner_a 两个项目 + owner_b 一个项目。
    await hasn_design_project_dao.create(
        db,
        CreateHasnDesignProjectParam(
            owner_hasn_id=owner_a,
            name='A 的海报项目',
            description='测试项目',
            canvas_meta={'width': 1080, 'height': 1920, 'page_count': 1},
            status='draft',
            visibility='private',
        ),
    )
    await hasn_design_project_dao.create(
        db,
        CreateHasnDesignProjectParam(
            owner_hasn_id=owner_a,
            name='A 的 UI 稿项目',
            canvas_meta={},
            status='active',
            visibility='private',
        ),
    )
    await hasn_design_project_dao.create(
        db,
        CreateHasnDesignProjectParam(
            owner_hasn_id=owner_b,
            name='B 的 Logo 项目',
            canvas_meta={},
            status='draft',
            visibility='shared',
        ),
    )
    await db.flush()

    # 查：行级隔离——owner_a 只看见自己的 2 条，owner_b 只看见自己的 1 条。
    rows_a = (
        (await db.execute(sa.select(HasnDesignProject).where(HasnDesignProject.owner_hasn_id == owner_a)))
        .scalars()
        .all()
    )
    rows_b = (
        (await db.execute(sa.select(HasnDesignProject).where(HasnDesignProject.owner_hasn_id == owner_b)))
        .scalars()
        .all()
    )
    assert len(rows_a) == 2, f'owner_a 应有 2 个项目，实际 {len(rows_a)}'
    assert len(rows_b) == 1, f'owner_b 应有 1 个项目，实际 {len(rows_b)}'
    assert {r.name for r in rows_a} == {'A 的海报项目', 'A 的 UI 稿项目'}
    # 字段真落盘（jsonb canvas_meta / 字典 status·visibility / 默认 created_time）。
    poster = next(r for r in rows_a if r.name == 'A 的海报项目')
    assert poster.canvas_meta == {'width': 1080, 'height': 1920, 'page_count': 1}
    assert poster.status == 'draft'
    assert poster.visibility == 'private'
    assert poster.created_time is not None
    assert rows_b[0].visibility == 'shared'

    # 删：删 owner_a 的项目，不影响 owner_b（隔离）。
    deleted = await hasn_design_project_dao.delete(db, [r.id for r in rows_a])
    assert deleted == 2
    await db.flush()
    rows_a_after = (
        (await db.execute(sa.select(HasnDesignProject).where(HasnDesignProject.owner_hasn_id == owner_a)))
        .scalars()
        .all()
    )
    rows_b_after = (
        (await db.execute(sa.select(HasnDesignProject).where(HasnDesignProject.owner_hasn_id == owner_b)))
        .scalars()
        .all()
    )
    assert len(rows_a_after) == 0
    assert len(rows_b_after) == 1, 'owner_b 项目不应被 owner_a 删除波及（行级隔离）'
