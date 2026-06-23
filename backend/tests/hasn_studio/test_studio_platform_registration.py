"""STUDIO-P2 — studio（OpenMontage 统一视频引擎接入，模块 14 doc22）AI-Native 平台**数据层 + 注册**真实测试（零 mock）。

本期 P2 范围（只数据层 + 目录/scope/manifest 骨架，不接 P3 service/工具 handler）：
- manifest 通过 ``validate_manifest``；进 ``_builtin_manifests``；cloud-brokered 形态；
  **本期 manifest 不暴露 tools/capabilities**（工具面随 P3 接 service + 云端 handler 后再补，避免声明指向
  尚不存在的 gateway 内部 handler，零 fake）。
- scopes.py 登记 5 个 studio scope（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）：
  read/write 出厂 default_mode=allow（risk=low）；render/export/share 出厂 default_mode=ask（花算力/外发）。
- studio scope **不进** ``DEFAULT_AGENT_SCOPES``（cloud-brokered 工具由 capability_modes 三态判定，非 JWT scope
  claim；同 finance/quant/creator）。
- App 形态（cloud / install_policy=manual / 视频工作台 /apps/studio / 个人模式）。
- catalog 出厂源：sort_order 76 / default_agent_type=content_operator（内容运营官）/ 无 per-app config_json。
- 真实 PG：4 表落 ``hasn_studio`` schema（不在 public）；``ensure_builtin_published`` 落库且 hash 自愈幂等；
  ``ensure_catalog_seeded`` 幂等播种。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/22-OpenMontage统一视频引擎(云服务·工具即服务)选型设计.md。
"""

from __future__ import annotations

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
    get_catalog,
)
from backend.app.hasn_studio.manifest import STUDIO_AI_NATIVE_MANIFEST, build_studio_app
from backend.app.hasn_studio.model._base import APP_SCHEMA, HasnStudioAppBase
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.security.agent_jwt import DEFAULT_AGENT_SCOPES
from backend.database.db import SQLALCHEMY_DATABASE_URL

_READ = 'studio:read'
_WRITE = 'studio:write'
_RENDER = 'studio:render'
_EXPORT = 'studio:export'
_SHARE = 'studio:share'

# 5 个 studio scope（= scopes.py / 全局 SCOPE_CATALOG 必须一致）。
_ALL_SCOPES = {_READ, _WRITE, _RENDER, _EXPORT, _SHARE}
# 出厂 allow（read/write）vs ask（render/export/share，花算力/外发）。
_ALLOW_SCOPES = {_READ, _WRITE}
_ASK_SCOPES = {_RENDER, _EXPORT, _SHARE}

# 4 张 hasn_studio 表（= SQL / model / codegen 三处一致）。
_STUDIO_TABLES = ('studio_project', 'studio_asset', 'studio_render_job', 'studio_artifact')


# ============================ 纯 Python（无 DB）============================


def test_studio_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(STUDIO_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_studio_in_builtin_registry() -> None:
    """studio 进 _builtin_manifests；cloud-brokered 形态；P3+P6 暴露 14 工具（read/write allow，render/export/share ask）。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'studio' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('studio')
    assert manifest['app_id'] == 'studio'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'cloud'
    assert manifest['transport_mode'] == 'cloud'
    # STUDIO-P3 工具面（12）+ STUDIO-P6 分享/发布（2）= 14 工具（= capabilities，gateway_internal handler）。
    assert len(manifest['tools']) == 14
    assert len(manifest['capabilities']) == 14


def test_studio_notifications_emit_declared() -> None:
    """studio 声明 notifications.emit（display_name=视频引擎）。"""
    emit = STUDIO_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['card_message'] is True
    assert emit['display_name'] == '视频引擎'


def test_studio_p3_tools_scope_three_states() -> None:
    """STUDIO-P3+P6：14 工具的 scope/三态出厂一致——read/write=allow（不确认），render/export/share=ask（确认）。"""
    expected_mcp_names = {
        'hasn.studio.list_pipelines',
        'hasn.studio.list_projects',
        'hasn.studio.get_project',
        'hasn.studio.list_assets',
        'hasn.studio.list_artifacts',
        'hasn.studio.get_render_job',
        'hasn.studio.save_project',
        'hasn.studio.save_storyboard',
        'hasn.studio.run_pipeline',
        'hasn.studio.render',
        'hasn.studio.run_tool',
        'hasn.studio.export',
        'hasn.studio.share',
        'hasn.studio.publish',
    }
    caps = STUDIO_AI_NATIVE_MANIFEST['capabilities']
    assert {c['mcp_name'] for c in caps} == expected_mcp_names

    by_name = {c['mcp_name']: c for c in caps}
    # read/write 出厂 allow（human_confirmation.required=False）。
    for name in (
        'hasn.studio.list_pipelines',
        'hasn.studio.list_projects',
        'hasn.studio.get_project',
        'hasn.studio.list_assets',
        'hasn.studio.list_artifacts',
        'hasn.studio.get_render_job',
        'hasn.studio.save_project',
        'hasn.studio.save_storyboard',
    ):
        assert by_name[name]['human_confirmation']['required'] is False, f'{name} 出厂应 allow'
    # render/run_pipeline/run_tool/export/share/publish 出厂 ask（human_confirmation.required=True，花算力/外发）。
    for name in (
        'hasn.studio.run_pipeline',
        'hasn.studio.render',
        'hasn.studio.run_tool',
        'hasn.studio.export',
        'hasn.studio.share',
        'hasn.studio.publish',
    ):
        assert by_name[name]['human_confirmation']['required'] is True, f'{name} 出厂应 ask'

    # required_scopes 对齐：read/write/render/export/share。
    assert by_name['hasn.studio.list_pipelines']['required_scopes'] == ['studio:read']
    assert by_name['hasn.studio.save_project']['required_scopes'] == ['studio:write']
    assert by_name['hasn.studio.run_pipeline']['required_scopes'] == ['studio:render']
    assert by_name['hasn.studio.run_tool']['required_scopes'] == ['studio:render']
    assert by_name['hasn.studio.export']['required_scopes'] == ['studio:export']
    assert by_name['hasn.studio.share']['required_scopes'] == ['studio:share']
    assert by_name['hasn.studio.publish']['required_scopes'] == ['studio:share']


def test_studio_p3_tool_handlers_registered_in_gateway() -> None:
    """STUDIO-P3：manifest 每个 tool.handler 都在 gateway _internal_handlers() 注册表里（零悬挂声明）。"""
    from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway

    registry = ai_native_runtime_gateway._internal_handlers()
    for tool in STUDIO_AI_NATIVE_MANIFEST['tools']:
        assert tool['transport'] == 'gateway_internal'
        assert tool['handler'] in registry, f'gateway 缺 handler: {tool["handler"]}'


def test_studio_workbench_app_shape() -> None:
    """App：cloud + 手动安装 + 个人模式 + 视频工作台 /apps/studio（非新窗口）。"""
    app = build_studio_app()
    assert app.id == 'studio'
    assert app.name == '视频引擎'
    assert app.execution_mode == 'cloud'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/studio'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(STUDIO_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert STUDIO_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_studio_scopes_registered_with_three_states() -> None:
    """scopes.py 登记 5 个 studio scope（聚合进全局 SCOPE_CATALOG），三态出厂默认正确。

    - read/write 出厂 default_mode=allow（只读/不出片，risk=low）；
    - render/export/share 出厂 default_mode=ask（花算力/外发，主人裁决）。
    """
    for key in _ALL_SCOPES:
        assert key in SCOPE_CATALOG, f'scope 未登记: {key}'
        assert scope_meta(key)['domain'] == 'studio'

    for key in _ALLOW_SCOPES:
        assert scope_meta(key)['default_mode'] == 'allow', f'{key} 出厂应 allow'

    for key in _ASK_SCOPES:
        assert scope_meta(key)['default_mode'] == 'ask', f'{key} 出厂应 ask（花算力/外发）'

    # 具体 label / risk 锚点（防漂移）。
    assert scope_meta(_READ)['label'] == '查看视频项目与成品'
    assert scope_meta(_READ)['risk'] == 'low'
    assert scope_meta(_WRITE)['label'] == '保存/更新视频项目'
    assert scope_meta(_RENDER)['risk'] == 'medium'
    assert scope_meta(_EXPORT)['risk'] == 'medium'
    assert scope_meta(_SHARE)['risk'] == 'low'


def test_studio_scopes_not_minted_into_jwt() -> None:
    """studio scope 不进 DEFAULT_AGENT_SCOPES（cloud-brokered 工具由 capability_modes 判定，非 JWT claim）。"""
    for key in _ALL_SCOPES:
        assert key not in DEFAULT_AGENT_SCOPES, f'cloud-brokered scope 不应铸入 JWT: {key}'


def test_studio_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order 76 / content_operator 默认承接 / 无 per-app config_json。"""
    assert _CATALOG_SORT_ORDER['studio'] == 76
    assert _CATALOG_AGENT_DEFAULTS['studio'][0] == 'content_operator'
    assert _CATALOG_AGENT_DEFAULTS['studio'][1]  # 业务提示词非空
    # studio 无平台级模型配置（引擎地址/令牌走云端 env MONTAGE_ENGINE_*，不进 catalog config_json）。
    assert 'studio' not in _CATALOG_DEFAULT_CONFIG


def test_studio_models_extend_app_base_and_schema() -> None:
    """4 个 model 都继承 HasnStudioAppBase 且落 hasn_studio schema（不落 public）。"""
    from backend.app.hasn_studio.model import StudioArtifact, StudioAsset, StudioProject, StudioRenderJob

    assert APP_SCHEMA == 'hasn_studio'
    for model in (StudioProject, StudioAsset, StudioRenderJob, StudioArtifact):
        assert issubclass(model, HasnStudioAppBase), f'{model.__name__} 未继承 HasnStudioAppBase'
        assert model.__table__.schema == 'hasn_studio', f'{model.__name__} 未落 hasn_studio schema'


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
async def test_studio_tables_in_hasn_studio_schema(db: AsyncSession) -> None:
    """4 张表都建在 hasn_studio schema，绝不落 public（ADR-15 应用独立 schema）。"""
    rows = (
        await db.execute(
            sa.text('SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = ANY(:names)'),
            {'names': list(_STUDIO_TABLES)},
        )
    ).all()
    got = {(r.table_schema, r.table_name) for r in rows}
    expected = {('hasn_studio', t) for t in _STUDIO_TABLES}
    leaked = {r for r in got if r[0] == 'public'}
    assert leaked == set(), f'表泄漏到 public: {leaked}'
    assert expected <= got, f'hasn_studio 缺表: {expected - got}'


@pytest.mark.asyncio
async def test_studio_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 studio manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'studio')
    assert published['app_id'] == 'studio'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(STUDIO_AI_NATIVE_MANIFEST)

    again = await ai_native_app_registry.ensure_builtin_published(db, 'studio')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_studio_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 studio catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='studio')
    assert cat is not None, 'studio catalog 行未播种'
    assert cat.app_id == 'studio'
    assert cat.name == '视频引擎'
    assert cat.execution_mode == 'cloud'
    assert cat.entry_route == '/apps/studio'
    assert cat.default_agent_type == 'content_operator'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'  # 免费 → 开箱即用（access 闸 access_type=free 直接放行）

    # 二次播种：不重复插（studio 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'studio')
        )
    ).scalar()
    assert cats == 1, f'studio catalog 行应唯一，实际 {cats}'
