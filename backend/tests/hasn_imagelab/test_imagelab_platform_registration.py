"""imagelab-P3 — imagelab（图坊，自研本地图像处理引擎，模块 14 doc30）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端注册第一刀（catalog/manifest 注册 + 铸七 scope，doc30 §5.5）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（16 个；读类 3 → imagelab:read、处理类 8 → imagelab:process、
  导出类 1 → imagelab:export、批量类 1 → imagelab:batch、破坏性类 1 → imagelab:destructive、
  生成类 1 → imagelab:generate、分享类 1 → imagelab:share；mcp_name 全 hasn.imagelab.*）。
- scopes.py 登记七 scope（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- 出厂默认三态：imagelab:read/:process/:export 出厂 Allow；imagelab:batch/:destructive/:generate/:share
  出厂 Ask（= imagelab.rs 本地出厂态，缺陷 3 修复契约——静息态与本地 CapabilityModeMirror 一致）。
- 跨仓零漂移：manifest 管理类（非 :read）required_scopes 集合 ==
  {imagelab:process, imagelab:export, imagelab:batch, imagelab:destructive, imagelab:generate, imagelab:share}
  （= hasn-node crates/hasn-mcp/src/imagelab.rs capability_scopes() 契约，见 test_local_tool_scope_alignment）。
- App 形态（local_tool / 手动安装 / 内联路由 /apps/imagelab；自研本地引擎按需装）。
- catalog 出厂源：sort_order / default_agent_type(content_operator，非专有分身) / config_json（只 engine 骨架 +
  按需下载 ML 模型清单，无 image/video 生成模型——生成桥接平台）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等；
  ``ensure_catalog_seeded`` 幂等播种 imagelab catalog 行（重复跑不重复插）+ config_json 经 app_configs 下发。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/30-图像处理AI-Native应用(自研引擎·图坊)架构设计.md §5.4/§5.5/§5.9。
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
    get_all_app_configs,
    get_catalog,
)
from backend.app.hasn_imagelab.manifest import IMAGELAB_AI_NATIVE_MANIFEST, build_imagelab_app
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 落地真相（hasn-node crates/hasn-mcp/src/imagelab.rs，P3 待落，本表是云端侧契约源）：
# 读类 3 / 处理类 8 / 导出类 1 / 批量类 1 / 破坏性类 1 / 生成类 1 / 分享类 1 = 16 个。
_READ_TOOLS = {'analyze', 'job.get', 'job.list'}
_PROCESS_TOOLS = {
    'process',
    'pipeline',
    'animate',
    'enhance',
    'recipe.save',
    'recipe.list',
    'recipe.get',
    'import',
}
_EXPORT_TOOLS = {'export'}
_BATCH_TOOLS = {'batch'}
_DESTRUCTIVE_TOOLS = {'retouch'}
_GENERATE_TOOLS = {'generate'}
_SHARE_TOOLS = {'share'}

_READ_SCOPE = 'imagelab:read'
_PROCESS_SCOPE = 'imagelab:process'
_BATCH_SCOPE = 'imagelab:batch'
_DESTRUCTIVE_SCOPE = 'imagelab:destructive'
_GENERATE_SCOPE = 'imagelab:generate'
_EXPORT_SCOPE = 'imagelab:export'
_SHARE_SCOPE = 'imagelab:share'

# 出厂 Allow（不弹确认）vs 出厂 Ask（human_confirmation.required=True）的工具集。
_ALLOW_TOOLS = _READ_TOOLS | _PROCESS_TOOLS | _EXPORT_TOOLS
_ASK_TOOLS = _BATCH_TOOLS | _DESTRUCTIVE_TOOLS | _GENERATE_TOOLS | _SHARE_TOOLS

# 每工具 → 期望 scope。
_TOOL_SCOPE = {
    **dict.fromkeys(_READ_TOOLS, _READ_SCOPE),
    **dict.fromkeys(_PROCESS_TOOLS, _PROCESS_SCOPE),
    **dict.fromkeys(_EXPORT_TOOLS, _EXPORT_SCOPE),
    **dict.fromkeys(_BATCH_TOOLS, _BATCH_SCOPE),
    **dict.fromkeys(_DESTRUCTIVE_TOOLS, _DESTRUCTIVE_SCOPE),
    **dict.fromkeys(_GENERATE_TOOLS, _GENERATE_SCOPE),
    **dict.fromkeys(_SHARE_TOOLS, _SHARE_SCOPE),
}


# ============================ 纯 Python（无 DB）============================


def test_imagelab_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(IMAGELAB_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_imagelab_in_builtin_registry() -> None:
    """imagelab 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'imagelab' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('imagelab')
    assert manifest['app_id'] == 'imagelab'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_imagelab_capabilities_match_landed_tools() -> None:
    """16 个 capability，mcp_name 全 hasn.imagelab.*；每工具 scope 与 §5.4 工具表逐一对齐。"""
    caps = IMAGELAB_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == set(_TOOL_SCOPE), f'工具集与落地不一致: {names}'
    assert len(caps) == 16

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.imagelab.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        assert cap['required_scopes'] == [_TOOL_SCOPE[short]], f'{short} scope 应为 {_TOOL_SCOPE[short]}'
        # 出厂 Allow（读/处理/导出）不弹确认；出厂 Ask（批量/破坏性/生成/分享）弹确认。
        expected_confirm = short in _ASK_TOOLS
        assert cap['human_confirmation'].get('required') is expected_confirm, f'{short} 确认态不符'


def test_imagelab_management_scopes_match_cross_repo_contract() -> None:
    """管理类（非 :read）required_scopes 集合 == 六个非读 scope（= imagelab.rs capability_scopes() 契约）。"""
    management = {
        scope
        for cap in IMAGELAB_AI_NATIVE_MANIFEST['capabilities']
        for scope in (cap.get('required_scopes') or [])
        if not scope.endswith(':read')
    }
    assert management == {
        _PROCESS_SCOPE,
        _EXPORT_SCOPE,
        _BATCH_SCOPE,
        _DESTRUCTIVE_SCOPE,
        _GENERATE_SCOPE,
        _SHARE_SCOPE,
    }


def test_imagelab_scopes_registered_in_catalog() -> None:
    """scopes.py 登记七 scope（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    for scope in _TOOL_SCOPE.values():
        assert scope in SCOPE_CATALOG, f'{scope} 未聚合进 SCOPE_CATALOG'
        assert scope_meta(scope)['domain'] == 'imagelab'
    assert scope_meta(_READ_SCOPE)['label'] == '读取与分析图片'
    assert scope_meta(_PROCESS_SCOPE)['label'] == '处理图片'
    assert scope_meta(_BATCH_SCOPE)['label'] == '批量处理图片'
    assert scope_meta(_DESTRUCTIVE_SCOPE)['label'] == '局部消除 / 去水印'
    assert scope_meta(_GENERATE_SCOPE)['label'] == '生成式处理图片'
    assert scope_meta(_EXPORT_SCOPE)['label'] == '导出到本地目录'
    assert scope_meta(_SHARE_SCOPE)['label'] == '分享产物到好友/群'


def test_imagelab_scope_factory_defaults_match_local_enforcement() -> None:
    """出厂默认成为唯一真相：read/process/export/batch/destructive/generate 出厂 Allow，share 出厂 Ask
    （= imagelab.rs 本地出厂态）。scope_meta(default_mode) 必须等于 hasn-mcp imagelab.rs 的
    default_capability_mode()，否则云端 catalog 静息态会与本地 CapabilityModeMirror 实际执行分裂。
    2026-07-05 策略「只拦外发/动钱，放开生成/委托」：batch/destructive/generate 都是处理/编辑/生成
    自己的图片（消耗配额≠动钱）→ 放开 Allow；只有 share（上云发好友/群=外发）保留 Ask。
    """
    assert scope_meta(_READ_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_PROCESS_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_EXPORT_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_BATCH_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_DESTRUCTIVE_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_GENERATE_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_SHARE_SCOPE)['default_mode'] == 'ask'


def test_imagelab_notifications_emit_declared() -> None:
    """imagelab 声明 notifications.emit（处理完成/派发卡摊给主人发卡，doc30 §4.2）。"""
    emit = IMAGELAB_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '图坊'


def test_imagelab_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（自研本地引擎按需装）+ 内联路由 /apps/imagelab（非新窗口）。"""
    app = build_imagelab_app()
    assert app.id == 'imagelab'
    assert app.name == '图坊'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/imagelab'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(IMAGELAB_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert IMAGELAB_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_imagelab_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order / content_operator 默认承接 / config_json engine 骨架。"""
    assert _CATALOG_SORT_ORDER['imagelab'] == 58
    assert _CATALOG_AGENT_DEFAULTS['imagelab'][0] == 'content_operator'
    assert _CATALOG_AGENT_DEFAULTS['imagelab'][1]  # 业务提示词非空

    cfg = _CATALOG_DEFAULT_CONFIG['imagelab']
    # 自研本地引擎：只有 engine 骨架 + 按需下载 ML 模型清单，绝无 image/video 生成模型（生成桥接平台）。
    assert 'models' not in cfg
    assert cfg['engine']['bundled_deps'] == ['ffmpeg', 'libwebp']
    assert 'birefnet-general' in cfg['engine']['ml_models']


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
async def test_imagelab_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 imagelab manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'imagelab')
    assert published['app_id'] == 'imagelab'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(IMAGELAB_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'imagelab')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_imagelab_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 imagelab catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='imagelab')
    assert cat is not None, 'imagelab catalog 行未播种'
    assert cat.app_id == 'imagelab'
    assert cat.name == '图坊'
    assert cat.execution_mode == 'local_tool'
    assert cat.entry_route == '/apps/imagelab'
    assert cat.default_agent_type == 'content_operator'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'
    # config_json：engine 骨架 + ML 模型清单（自研本地引擎无 image/video 生成模型）。
    assert 'models' not in cat.config_json
    assert cat.config_json['engine']['bundled_deps'] == ['ffmpeg', 'libwebp']

    # 二次播种：不重复插（imagelab 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'imagelab')
        )
    ).scalar()
    assert cats == 1, f'imagelab catalog 行应唯一，实际 {cats}'


@pytest.mark.asyncio
async def test_imagelab_config_in_app_configs_downlink(db: AsyncSession) -> None:
    """app_configs.imagelab 经 get_all_app_configs 聚合下发（daemon 据此读配置注入 sidecar）。"""
    await ensure_catalog_seeded(db)
    app_configs = await get_all_app_configs(db)
    assert 'imagelab' in app_configs, 'imagelab 未进 app_configs 下发聚合'
    imagelab_cfg = app_configs['imagelab']
    assert imagelab_cfg['engine']['bundled_deps'] == ['ffmpeg', 'libwebp']
    assert 'birefnet-general' in imagelab_cfg['engine']['ml_models']
