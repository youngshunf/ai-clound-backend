"""reel-P4 — reel（短视频合成应用，源自 MoneyPrinterTurbo，模块 14 doc19）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端第一刀（catalog/manifest 注册 + 铸 scope + PDC app_configs，实施 11 P2/P4）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（8 个；读类 3 → reel:read、写类 4 → reel:write、
  导出类 artifact.upload → reel:export；mcp_name 全 hasn.reel.*）。
- scopes.py 登记 reel:read/:write/:export（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- scope 授权走三态 capability_modes（JWT scopes claim 已退役，实施102 S0）：reel:read/:write/:export
  由 ``hasn_agent_scopes.{default_mode, capability_modes}`` 消费时活取判定，凭证不再承载 scope。
- 跨仓零漂移：manifest 管理类（非 :read）required_scopes 集合 == {reel:write, reel:export}
  （= hasn-node crates/hasn-mcp/src/reel.rs capability_scopes() 契约，见 test_local_tool_scope_alignment）。
- App 形态（local_tool / 手动安装 / 瘦引擎页 /apps/reel；瘦引擎应用按需装，内容创作在创作运营）。
- catalog 出厂源：sort_order / default_agent_type(content_operator) / config_json（仅 llm+tts+stt 三类模型，
  无 image/video；bundled_deps=ffmpeg/imagemagick）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等；
  ``ensure_catalog_seeded`` 幂等播种 reel catalog 行（重复跑不重复插）+ config_json 经 app_configs 下发。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/19-MoneyPrinterTurbo短视频合成应用接入设计.md §3/§6.4/§7；
        docs/hasn-node设计文档/14-AI-Native应用平台/实施/11-MoneyPrinterTurbo短视频合成应用接入实施清单.md P2/P4。
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
from backend.app.hasn_reel.manifest import REEL_AI_NATIVE_MANIFEST, build_reel_app
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 落地真相（hasn-node crates/hasn-mcp/src/reel.rs，reel-P4 待落，本表是云端侧契约源）：读类 3 / 写类 4 / 导出类 1。
_READ_TOOLS = {'task.list', 'task.get', 'material.search'}
_WRITE_TOOLS = {'generate', 'script.draft', 'preview', 'compose'}
_EXPORT_TOOLS = {'artifact.upload'}
_READ_SCOPE = 'reel:read'
_WRITE_SCOPE = 'reel:write'
_EXPORT_SCOPE = 'reel:export'


# ============================ 纯 Python（无 DB）============================


def test_reel_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(REEL_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_reel_in_builtin_registry() -> None:
    """reel 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'reel' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('reel')
    assert manifest['app_id'] == 'reel'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_reel_capabilities_match_landed_tools() -> None:
    """8 个 capability，mcp_name 全 hasn.reel.*；读类 reel:read、写类 reel:write、导出类 reel:export。"""
    caps = REEL_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _READ_TOOLS | _WRITE_TOOLS | _EXPORT_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 8

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.reel.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _READ_TOOLS:
            assert cap['required_scopes'] == [_READ_SCOPE], f'{short} 读类应为 reel:read'
            assert cap['human_confirmation'].get('required') is False
        elif short in _EXPORT_TOOLS:
            assert cap['required_scopes'] == [_EXPORT_SCOPE], f'{short} 导出类应为 reel:export'
            assert cap['human_confirmation'].get('required') is True
        else:
            assert cap['required_scopes'] == [_WRITE_SCOPE], f'{short} 写类应为 reel:write'
            # 出厂 Ask（合成花配额/本地资源）；owner 可经 capability_modes 设 allow/deny override。
            assert cap['human_confirmation'].get('required') is True


def test_reel_management_scopes_match_cross_repo_contract() -> None:
    """管理类（非 :read）required_scopes 集合 == {reel:write, reel:export}（= reel.rs capability_scopes() 契约）。"""
    management = {
        scope
        for cap in REEL_AI_NATIVE_MANIFEST['capabilities']
        for scope in (cap.get('required_scopes') or [])
        if not scope.endswith(':read')
    }
    assert management == {_WRITE_SCOPE, _EXPORT_SCOPE}


def test_reel_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 reel:read/:write/:export（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _EXPORT_SCOPE):
        assert scope in SCOPE_CATALOG
        assert scope_meta(scope)['domain'] == 'reel'
    assert scope_meta(_READ_SCOPE)['label'] == '查看短视频任务'
    assert scope_meta(_WRITE_SCOPE)['label'] == '生成短视频'
    assert scope_meta(_EXPORT_SCOPE)['label'] == '上传/分享短视频成片'


def test_reel_scope_factory_defaults_match_local_enforcement() -> None:
    """出厂默认成为唯一真相：reel:read 出厂 Allow，reel:write/:export 出厂 Ask（= reel.rs 本地出厂态）。

    scope_meta(default_mode) 必须等于 hasn-mcp reel.rs 的 default_capability_mode()，否则云端 catalog
    静息态会与本地 CapabilityModeMirror 实际执行分裂（权限页显示「允许」但每次调用仍审批）。
    """
    assert scope_meta(_READ_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_WRITE_SCOPE)['default_mode'] == 'ask'
    assert scope_meta(_EXPORT_SCOPE)['default_mode'] == 'ask'


def test_reel_notifications_emit_declared() -> None:
    """reel 声明 notifications.emit（成片就绪/文案停点摊给主人发卡，实施 11 P5）。"""
    emit = REEL_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '短视频合成'


def test_reel_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（瘦引擎应用按需装）+ 瘦引擎页 /apps/reel（非新窗口）。"""
    app = build_reel_app()
    assert app.id == 'reel'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/reel'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(REEL_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert REEL_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_reel_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order / content_operator 默认承接 / config_json 三类模型。"""
    assert _CATALOG_SORT_ORDER['reel'] == 57
    assert _CATALOG_AGENT_DEFAULTS['reel'][0] == 'content_operator'
    assert _CATALOG_AGENT_DEFAULTS['reel'][1]  # 业务提示词非空

    cfg = _CATALOG_DEFAULT_CONFIG['reel']
    # 合成式：只有 llm（文案/搜索词）+ tts（配音）+ stt（字幕，可选）三类模型，绝无 image/video 生成模型。
    assert set(cfg['models'].keys()) == {'llm', 'tts', 'stt'}
    assert 'image_t2i' not in cfg['models'] and 'video' not in cfg['models']
    # reel 特有本地合成依赖。
    assert cfg['engine']['bundled_deps'] == ['ffmpeg', 'imagemagick']
    # TTS 默认 edge 免费；字幕默认 edge（不下 whisper）。
    assert cfg['tts']['tts_provider'] == 'edge'
    assert cfg['subtitle']['subtitle_provider'] == 'edge'
    # 素材平台兜底 key 留空占位（绝不硬编码真实 key）。
    assert cfg['material']['platform_keys'] == {'pexels': [], 'pixabay': []}


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
async def test_reel_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 reel manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'reel')
    assert published['app_id'] == 'reel'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(REEL_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'reel')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_reel_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 reel catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='reel')
    assert cat is not None, 'reel catalog 行未播种'
    assert cat.app_id == 'reel'
    assert cat.name == '短视频合成'
    assert cat.execution_mode == 'local_tool'
    assert cat.entry_route == '/apps/reel'
    assert cat.default_agent_type == 'content_operator'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'
    # config_json：三类模型 + bundled_deps（合成式无 image/video）。
    assert set(cat.config_json['models'].keys()) == {'llm', 'tts', 'stt'}
    assert cat.config_json['engine']['bundled_deps'] == ['ffmpeg', 'imagemagick']

    # 二次播种：不重复插（reel 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'reel'))
    ).scalar()
    assert cats == 1, f'reel catalog 行应唯一，实际 {cats}'


@pytest.mark.asyncio
async def test_reel_config_in_app_configs_downlink(db: AsyncSession) -> None:
    """app_configs.reel 经 get_all_app_configs 聚合下发（daemon 据此从 app_configs.reel 读配置注入 sidecar）。"""
    await ensure_catalog_seeded(db)
    app_configs = await get_all_app_configs(db)
    assert 'reel' in app_configs, 'reel 未进 app_configs 下发聚合'
    reel_cfg = app_configs['reel']
    assert set(reel_cfg['models'].keys()) == {'llm', 'tts', 'stt'}
    assert reel_cfg['tts']['tts_provider'] == 'edge'
    assert reel_cfg['material']['platform_keys'] == {'pexels': [], 'pixabay': []}
