"""VC-P4 — film（视频生成应用，源自 VideoClaw，模块 14）AI-Native 平台接入 真实测试（零 mock）。

覆盖 P4 云端第一刀（catalog/manifest 注册 + 铸 scope，实施 10 P4）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（17 个；读类 → film:read、写类 13 → film:write、
  导出类 artifact.upload → film:export；mcp_name 全 hasn.film.*）。
- scopes.py 登记 film:read/:write/:export（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- **铸 scope**：film:read/:write/:export 进 DEFAULT_AGENT_SCOPES（JWT scopes claim 唯一固定来源），
  且 Agent JWT 编解码忠实携带三者——否则 Agent 调云端 film/agent/* 写类被 check_scopes 403。
- 跨仓零漂移：manifest 管理类（非 :read）required_scopes 集合 == {film:write, film:export}
  （= hasn-node crates/hasn-mcp/src/film.rs capability_scopes() 契约，见 test_local_tool_scope_alignment）。
- WorkbenchApp 形态（local_tool / 手动安装 / 内联路由；本期不自动挂载，入口随 P8 webui+catalog 落地）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/18-VideoClaw视频生成应用接入与按需下载形态设计.md §7；
        docs/hasn-node设计文档/14-AI-Native应用平台/实施/10-VideoClaw视频生成应用接入实施清单.md P4。
"""

from __future__ import annotations

import uuid

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn_film.manifest import FILM_AI_NATIVE_MANIFEST, build_film_workbench_app
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.security.agent_jwt import (
    DEFAULT_AGENT_SCOPES,
    jwt_decode_agent,
    jwt_encode_agent,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

# 落地真相（hasn-node crates/hasn-mcp/src/film.rs）：读类 3 / 写类 13 / 导出类 1。
_READ_TOOLS = {'project.list', 'project.get', 'stage.artifact'}
_WRITE_TOOLS = {
    'project.create',
    'script.generate',
    'character.design',
    'storyboard.create',
    'reference.generate',
    'clip.generate',
    'post.compose',
    'stage.intervene',
    'stage.continue',
    'sandbox.t2i',
    'sandbox.i2i',
    'sandbox.short',
    'pipeline.run',
}
_EXPORT_TOOLS = {'artifact.upload'}
_READ_SCOPE = 'film:read'
_WRITE_SCOPE = 'film:write'
_EXPORT_SCOPE = 'film:export'


# ============================ 纯 Python（无 DB）============================


def test_film_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(FILM_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_film_in_builtin_registry() -> None:
    """film 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'film' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('film')
    assert manifest['app_id'] == 'film'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_film_capabilities_match_landed_tools() -> None:
    """17 个 capability，mcp_name 全 hasn.film.*；读类 film:read、写类 film:write、导出类 film:export。"""
    caps = FILM_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _READ_TOOLS | _WRITE_TOOLS | _EXPORT_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 17

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.film.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _READ_TOOLS:
            assert cap['required_scopes'] == [_READ_SCOPE], f'{short} 读类应为 film:read'
            assert cap['human_confirmation'].get('required') is False
        elif short in _EXPORT_TOOLS:
            assert cap['required_scopes'] == [_EXPORT_SCOPE], f'{short} 导出类应为 film:export'
            assert cap['human_confirmation'].get('required') is True
        else:
            assert cap['required_scopes'] == [_WRITE_SCOPE], f'{short} 写类应为 film:write'
            # 出厂 Ask（视频/参考图花钱）；owner 可经 capability_modes 设 allow/deny override。
            assert cap['human_confirmation'].get('required') is True


def test_film_management_scopes_match_cross_repo_contract() -> None:
    """管理类（非 :read）required_scopes 集合 == {film:write, film:export}（= film.rs capability_scopes() 契约）。"""
    management = {
        scope
        for cap in FILM_AI_NATIVE_MANIFEST['capabilities']
        for scope in (cap.get('required_scopes') or [])
        if not scope.endswith(':read')
    }
    assert management == {_WRITE_SCOPE, _EXPORT_SCOPE}


def test_film_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 film:read/:write/:export（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _EXPORT_SCOPE):
        assert scope in SCOPE_CATALOG
        assert scope_meta(scope)['domain'] == 'film'
    assert scope_meta(_READ_SCOPE)['label'] == '查看视频项目'
    assert scope_meta(_WRITE_SCOPE)['label'] == '生成与编辑视频'
    assert scope_meta(_EXPORT_SCOPE)['label'] == '上传/分享视频产物'


def test_film_scope_factory_defaults_match_local_enforcement() -> None:
    """出厂默认成为唯一真相：film:read 出厂 Allow，film:write/:export 出厂 Ask（= film.rs 本地出厂态）。

    缺陷 3 修复契约：scope_meta(default_mode) 必须等于 hasn-mcp film.rs 的
    default_capability_mode()，否则云端 catalog 静息态会与本地 CapabilityModeMirror
    实际执行分裂（权限页显示「允许」但每次调用仍审批）。
    """
    assert scope_meta(_READ_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_WRITE_SCOPE)['default_mode'] == 'ask'
    assert scope_meta(_EXPORT_SCOPE)['default_mode'] == 'ask'


def test_film_scopes_minted_into_agent_jwt() -> None:
    """铸 scope：film:read/:write/:export 进 DEFAULT_AGENT_SCOPES，且 Agent JWT 编解码忠实携带三者。

    生产真相：JWT scopes claim 的唯一固定来源是 DEFAULT_AGENT_SCOPES（agent_jwt.py）。Agent 调云端
    film/agent/* 写类时 check_scopes 据此放行——故三者必须在该常量内，并经真实 encode/decode 存活。
    """
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _EXPORT_SCOPE):
        assert scope in DEFAULT_AGENT_SCOPES

    expire = timezone.now() + timedelta(seconds=3600)
    payload = {
        'sub': 'a_film_expert',
        'token_type': 'agent',
        'agent_hasn_id': 'a_film_expert',
        'agent_name': '视频创作专家',
        'owner_hasn_id': 'h_test_owner',
        'owner_user_id': 1,
        'scopes': list(DEFAULT_AGENT_SCOPES),
        'session_uuid': str(uuid.uuid4()),
        'exp': timezone.to_utc(expire).timestamp(),
    }
    decoded = jwt_decode_agent(jwt_encode_agent(payload))
    for scope in (_READ_SCOPE, _WRITE_SCOPE, _EXPORT_SCOPE):
        assert scope in decoded.scopes


def test_film_notifications_emit_declared() -> None:
    """film 声明 notifications.emit（生成完成/停点摊给主人发卡，实施 10 P4）。"""
    emit = FILM_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '视频生成'


def test_film_workbench_app_shape() -> None:
    """WorkbenchApp：local_tool + 手动安装（本期不自动挂载）+ 内联路由（非新窗口）。"""
    app = build_film_workbench_app()
    assert app.id == 'film'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/workbench/apps/film'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(FILM_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert FILM_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


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
async def test_film_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 film manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'film')
    assert published['app_id'] == 'film'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(FILM_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'film')
    assert again['manifest_hash'] == published['manifest_hash']
