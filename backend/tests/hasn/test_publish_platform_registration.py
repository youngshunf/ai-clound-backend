"""publish（通用网页发布与分享，模块 18）AI-Native 平台接入 真实测试（零 mock）。

覆盖把 web-publish 注册成标准 AI-Native 应用（manifest 落 publish 目录 + 注册 hasn_app_catalog）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（写类 publish:write / 读类无 scope；7 个；mcp_name 全 hasn.publish.*）。
- scopes.py 登记 publish:read / publish:write。
- WorkbenchApp 形态（local_tool / 自动安装 / 内联路由 /publish）。
- 真实 PG：``ensure_catalog_seeded`` 播种 publish 行（local_tool/builtin/free/auto/sort 40）；
  ``ensure_builtin_published`` 把 publish manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等。

事实源: docs/hasn-node设计文档/18-通用网页发布与分享/；
        docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.app.publish.manifest import PUBLISH_AI_NATIVE_MANIFEST, build_publish_workbench_app
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# 落地真相（hasn-node crates/hasn-mcp/src/publish.rs）：写类 5 工具 / 读类 2 工具。
_WRITE_TOOLS = {'create', 'update', 'set_visibility', 'revoke', 'delete'}
_READ_TOOLS = {'get', 'list'}
_MANAGE_SCOPE = 'publish:write'


# ============================ 纯 Python（无 DB）============================


def test_publish_manifest_validates() -> None:
    """publish manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(PUBLISH_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_publish_in_builtin_registry() -> None:
    """publish 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'publish' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('publish')
    assert manifest['app_id'] == 'publish'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_publish_capabilities_match_landed_tools() -> None:
    """7 个 capability，mcp_name 全 hasn.publish.*；写类 publish:write、读类无 scope（与 publish.rs 一致）。"""
    caps = PUBLISH_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _WRITE_TOOLS | _READ_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 7

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.publish.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _WRITE_TOOLS:
            assert cap['required_scopes'] == [_MANAGE_SCOPE], f'{short} 写类应为 publish:write'
            # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可设 ask/deny override；敏感可见性另有执行期硬闸。
            assert cap['human_confirmation'].get('required') is False
        else:
            assert cap['required_scopes'] == [], f'{short} 读类应无 required_scopes（与落地空 scope 一致）'
            assert cap['human_confirmation'].get('required') is False


def test_publish_scopes_registered() -> None:
    """scopes.py 登记 publish:read / publish:write（展示元数据）。"""
    assert 'publish:read' in SCOPE_CATALOG
    assert 'publish:write' in SCOPE_CATALOG
    write_meta = scope_meta('publish:write')
    assert write_meta['domain'] == 'publish'
    assert write_meta['label'] == '发布与管理网页'


def test_publish_workbench_app_shape() -> None:
    """WorkbenchApp：local_tool + 自动安装（免费注册即用）+ 内联路由 /publish（非新窗口）。"""
    app = build_publish_workbench_app()
    assert app.id == 'publish'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'auto'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/publish'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(PUBLISH_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert PUBLISH_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


# ============================ 真实 PostgreSQL ============================


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
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
async def test_publish_seeded_into_catalog(db: AsyncSession) -> None:
    """ensure_catalog_seeded 把 publish 落 hasn_app_catalog（local_tool/builtin/free/auto/sort 40）。"""
    await ensure_catalog_seeded(db)
    row = (
        await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'publish'))
    ).scalars().first()
    assert row is not None, 'publish catalog 行应被播种'
    assert row.source == 'builtin'
    assert row.status == 'published'
    assert row.execution_mode == 'local_tool'
    assert row.access_type == 'free'
    assert row.default_mount is True, '免费注册即用（install_policy=auto）'
    assert row.sort_order == 40
    assert row.entry_route == '/publish'
    assert 'personal' in (row.scope or [])
    assert row.manifest_present is True


@pytest.mark.asyncio
async def test_publish_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 publish manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'publish')
    assert published['app_id'] == 'publish'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(PUBLISH_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'publish')
    assert again['manifest_hash'] == published['manifest_hash']
