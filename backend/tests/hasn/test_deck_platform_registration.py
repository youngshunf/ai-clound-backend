"""deck（自研演示文稿，模块 17）AI-Native 平台接入 真实测试（零 mock）。

覆盖 DECK-P6 第一刀（manifest 落 deck 目录 + 注册到 hasn_app_catalog）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（写类 deck:manage / 读类无 scope；12 个；mcp_name 全 hasn.deck.*）。
- scopes.py 登记 deck:manage（早期占位 deck:read/deck:write 已删）。
- App 形态（local_tool / 手动安装 / 内联路由）。
- 真实 PG：``ensure_catalog_seeded`` 播种 deck 行（local_tool/builtin/free/auto/sort 35）；
  ``ensure_builtin_published`` 把 deck manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等。

事实源: docs/hasn-node设计文档/17-演示文稿系统/07-安全权限与平台接入.md §6；
        docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded
from backend.app.hasn_deck.manifest import DECK_AI_NATIVE_MANIFEST, build_deck_app
from backend.app.hasn_deck.service.deck_service import deck_service
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 落地真相（云端 backend/app/mcp/tools/deck.py，TOOLMIG2-P3 后本地 hasn-mcp deck 工具已删）：写类 9 工具 / 读类 4 工具。
_WRITE_TOOLS = {
    'create',
    'delete',
    'finalize',
    'outline.set',
    'page.write_batch',
    'page.write',
    'page.edit',
    'page.delete',
    'page.reorder',
}
_READ_TOOLS = {'get', 'list', 'style.list', 'style.get'}
_MANAGE_SCOPE = 'deck:manage'


# ============================ 纯 Python（无 DB）============================


def test_deck_manifest_validates() -> None:
    """deck manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(DECK_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_deck_in_builtin_registry() -> None:
    """deck 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'deck' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('deck')
    assert manifest['app_id'] == 'deck'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_deck_capabilities_match_landed_tools() -> None:
    """13 个 capability，mcp_name 全 hasn.deck.*；写类 deck:manage、读类无 scope（与云端 deck.py 一致）。"""
    caps = DECK_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _WRITE_TOOLS | _READ_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 13

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.deck.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _WRITE_TOOLS:
            assert cap['required_scopes'] == [_MANAGE_SCOPE], f'{short} 写类应为 deck:manage'
            # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可经 capability_modes 设 ask/deny override。
            assert cap['human_confirmation'].get('required') is False
        else:
            assert cap['required_scopes'] == [], f'{short} 读类应无 required_scopes（与落地空 scope 一致）'
            assert cap['human_confirmation'].get('required') is False


def test_deck_manage_scope_registered() -> None:
    """scopes.py 登记 deck:manage（早期占位 deck:read/deck:write 已删，避免假闸门）。"""
    assert 'deck:manage' in SCOPE_CATALOG
    assert 'deck:read' not in SCOPE_CATALOG
    assert 'deck:write' not in SCOPE_CATALOG
    meta = scope_meta('deck:manage')
    assert meta['label'] == '管理演示文稿'
    assert meta['domain'] == 'deck'


def test_deck_notifications_emit_declared() -> None:
    """deck 声明 notifications.emit（生成完成/导出就绪发卡，[07] §6.3）。"""
    emit = DECK_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '演示文稿'


def test_deck_workbench_app_shape() -> None:
    """App：local_tool + 自动挂载（唯一默认演示文稿应用）+ 内联路由（非新窗口）。"""
    app = build_deck_app()
    assert app.id == 'deck'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'auto'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/deck'
    assert app.ui_kind is None
    assert app.project_aware is True
    assert app.project_required is False
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(DECK_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert DECK_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode
    assert DECK_AI_NATIVE_MANIFEST['project_aware'] is True
    assert DECK_AI_NATIVE_MANIFEST['project_required'] is False


def test_deck_container_linkage_adapter_registered() -> None:
    """deck 容器挂靠点使用云端权威 id、owner 隔离和 platform_project_id 列。"""
    adapter = project_linkage_registry.get('deck')
    assert adapter is not None
    assert adapter.model.__name__ == 'Deck'
    assert adapter.id_column == 'id'
    assert adapter.owner_column == 'owner_id'
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.is_container is True


# ============================ 真实 PostgreSQL ============================


@pytest_asyncio.fixture
async def db():
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
async def test_deck_seeded_into_catalog(db) -> None:
    """ensure_catalog_seeded 把 deck 落 hasn_app_catalog（local_tool/builtin/free/auto/sort 35）。"""
    await ensure_catalog_seeded(db)
    row = (
        await db.execute(sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == 'deck'))
    ).scalars().first()
    assert row is not None, 'deck catalog 行应被播种'
    assert row.source == 'builtin'
    assert row.status == 'published'
    assert row.execution_mode == 'local_tool'
    assert row.access_type == 'free'
    assert row.default_mount is True, 'deck 自动挂载（唯一默认演示文稿应用）'
    assert row.sort_order == 35
    assert row.entry_route == '/apps/deck'
    assert 'personal' in (row.scope or [])


@pytest.mark.asyncio
async def test_deck_builtin_published_and_self_heals(db) -> None:
    """ensure_builtin_published 落 deck manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'deck')
    assert published['app_id'] == 'deck'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(DECK_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'deck')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_deck_create_mounts_owned_project_and_rejects_cross_owner(db) -> None:
    """真实 PG：Deck 创建挂入本人项目并进入联邦挂靠并集，跨 owner 项目必须拒绝。"""
    suffix = uuid.uuid4().hex[:12]
    owner = f'h_deck_project_{suffix}'
    project = await project_service.create_project(db, owner=owner, data={'name': '演示文稿归集测试'})

    deck = await deck_service.create_deck(
        db,
        owner_id=owner,
        title='项目复盘',
        platform_project_id=project['id'],
    )
    assert deck['platform_project_id'] == project['id']

    linked = await project_linkage_registry.list_linked_resources(
        db,
        owner=owner,
        project_id=project['id'],
    )
    assert any(item['resource_uri'] == f"hasn://deck/{deck['id']}" for item in linked)

    with pytest.raises(errors.NotFoundError):
        await deck_service.create_deck(
            db,
            owner_id=f'h_other_{suffix}',
            title='越权挂靠',
            platform_project_id=project['id'],
        )
