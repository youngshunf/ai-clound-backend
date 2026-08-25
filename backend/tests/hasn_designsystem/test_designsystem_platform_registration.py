"""DS-P7 — designsystem（自研设计系统生成应用，模块 14/20）AI-Native 平台接入 真实测试（零 mock）。

覆盖 P7 第一刀（catalog/manifest 注册 + 铸 scope，doc12 P7）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（写类 import/save → designsystem:write；读类 + 确定性纯函数
  compile_tokens/derive/validate/extract_components/list/get 无 scope；8 个；mcp_name 全 hasn.designsystem.*）。
- scopes.py 登记 designsystem:write/:publish（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- App 形态（local_tool / 手动安装 / 内联路由；本期不自动挂载，入口随 P8 webui+catalog 落地）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/20-设计系统生成应用(自研)架构设计.md §7；
        docs/hasn-node设计文档/14-AI-Native应用平台/实施/12-设计系统生成应用实施清单.md P7。
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

from backend.app.hasn.service.ai_native_app_registry import _manifest_hash, ai_native_app_registry
from backend.app.hasn.service import sync_invalidate_service
from backend.app.hasn_designsystem.model.design_system import DesignSystem
from backend.app.hasn_designsystem.manifest import (
    DESIGNSYSTEM_AI_NATIVE_MANIFEST,
    build_designsystem_app,
)
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

# 落地真相：写类 2 工具（import/save）/ 读类（含纯函数 + 场景自查）7 工具。
# check_scenes 是云端专属自查工具（DSGAL；读设计系统 required_scenes × 当前 HTML 实检覆盖 → 缺什么/怎么补），
# 无本地 Rust 孪生（读云端 DB，非纯函数），读类无 scope。
# ⚠️ 不再手抄工具名单。原先这里写死 _WRITE_TOOLS/_READ_TOOLS 两个集合，与 manifest 对账——两边都是
# 手抄的，于是 `get_gallery` 工具落地了、manifest 没登记、这份名单也没写，三方「一致」地漏掉同一个，
# 而测试名叫 match_landed_tools 却一路绿到 2026-08-25 才被发现。对账基准必须是**落地真相**
# （DESIGNSYSTEM_TOOLS），不是另一份人写的清单。
_WRITE_SCOPE = 'designsystem:write'
_PUBLISH_SCOPE = 'designsystem:publish'


# ============================ 纯 Python（无 DB）============================


def test_designsystem_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(DESIGNSYSTEM_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_designsystem_in_builtin_registry() -> None:
    """designsystem 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'designsystem' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('designsystem')
    assert manifest['app_id'] == 'designsystem'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_designsystem_capabilities_match_landed_tools() -> None:
    """manifest 的 capability 集合必须与**落地工具集**逐个对上，且 scope 与工具自己声明的一致。

    对账基准是 `DESIGNSYSTEM_TOOLS`（落地真相）而非手抄名单：新增/退役一个工具却忘了动 manifest，
    这条当场红——这正是 `get_gallery` 曾经漏登记而无人发现的那道缺口。
    """
    from backend.app.mcp.tools.designsystem import DESIGNSYSTEM_TOOLS

    caps = DESIGNSYSTEM_AI_NATIVE_MANIFEST['capabilities']
    cap_names = {c['mcp_name'] for c in caps}
    landed = {t.name for t in DESIGNSYSTEM_TOOLS}
    assert cap_names == landed, (
        f'manifest 与落地工具集不一致——只在工具里: {sorted(landed - cap_names)}；'
        f'只在 manifest 里: {sorted(cap_names - landed)}'
    )
    assert len(caps) == len(cap_names), 'capability 的 mcp_name 有重复'

    scope_by_name = {t.name: t.required_scopes for t in DESIGNSYSTEM_TOOLS}
    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.designsystem.'), cap['mcp_name']
        # scope 与工具**自己声明的**逐字对齐（此前按「写类/读类」两个手抄集合分流，
        # 分错类就永远发现不了）。
        assert cap['required_scopes'] == scope_by_name[cap['mcp_name']], cap['mcp_name']
        # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可经 capability_modes 设 ask/deny override。
        assert cap['human_confirmation'].get('required') is False


def test_designsystem_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 designsystem:write/:publish（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    assert _WRITE_SCOPE in SCOPE_CATALOG
    assert _PUBLISH_SCOPE in SCOPE_CATALOG
    assert scope_meta(_WRITE_SCOPE)['domain'] == 'designsystem'
    assert scope_meta(_WRITE_SCOPE)['label'] == '管理设计系统'
    assert scope_meta(_PUBLISH_SCOPE)['label'] == '发布/分享设计系统'


def test_designsystem_notifications_emit_declared() -> None:
    """designsystem 声明 notifications.emit（生成完成/分享发卡，[20] §7 / P10）。"""
    emit = DESIGNSYSTEM_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '设计系统'


def test_designsystem_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（本期不自动挂载）+ 内联路由（非新窗口）。"""
    app = build_designsystem_app()
    assert app.id == 'designsystem'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/designsystem'
    assert app.ui_kind is None
    assert app.project_aware is True
    assert app.project_required is False
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(DESIGNSYSTEM_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert DESIGNSYSTEM_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode
    assert DESIGNSYSTEM_AI_NATIVE_MANIFEST['project_aware'] is True
    assert DESIGNSYSTEM_AI_NATIVE_MANIFEST['project_required'] is False


def test_designsystem_project_linkage_adapter_registered() -> None:
    """设计系统根资源作为容器挂靠点进入统一项目注册表。"""
    assert hasattr(DesignSystem, 'platform_project_id')
    adapter = project_linkage_registry.get('designsystem')
    assert adapter is not None
    assert adapter.model is DesignSystem
    assert adapter.owner_column == 'owner_hasn_id'
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.is_container is True
    assert adapter.app_id == 'designsystem'
    assert adapter.deleted_column == 'deleted_time'
    assert adapter.sync_kind == 'designsystem'
    assert adapter.sync_scope == 'global'


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
async def test_designsystem_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 designsystem manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'designsystem')
    assert published['app_id'] == 'designsystem'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(DESIGNSYSTEM_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'designsystem')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_designsystem_project_linkage_changes_revision_and_hides_deleted_rows(db: AsyncSession) -> None:
    """显式挂靠/摘除可用且改变同步指纹；软删容器不可定位，也不进入项目聚合。"""
    tag = f'{id(db):x}'
    owner = f'h_ds_link_{tag}'
    project = await project_service.create_project(db, owner=owner, data={'name': f'项目 {tag}'})
    row = DesignSystem(
        owner_hasn_id=owner,
        name=f'设计系统 {tag}',
        slug=f'ds-link-{tag}',
        source_kind='generated',
        content_hash=f'hash-{tag}',
    )
    db.add(row)
    await db.flush()
    uri = f'hasn://designsystem/{row.id}'

    before = await sync_invalidate_service.compute_designsystem_revision(db)
    linked = await project_linkage_registry.link(
        db,
        owner=owner,
        resource_uri=uri,
        project_id=project['id'],
    )
    assert linked['changed'] is True
    after_link = await sync_invalidate_service.compute_designsystem_revision(db)
    assert after_link != before
    linked_resources = await project_linkage_registry.list_linked_resources(
        db,
        owner=owner,
        project_id=project['id'],
    )
    assert {item['resource_uri'] for item in linked_resources} == {uri}

    unlinked = await project_linkage_registry.unlink(
        db,
        owner=owner,
        resource_uri=uri,
        project_id=project['id'],
    )
    assert unlinked['changed'] is True
    after_unlink = await sync_invalidate_service.compute_designsystem_revision(db)
    assert after_unlink == before

    row.platform_project_id = project['id']
    row.deleted_time = timezone.now()
    await db.flush()
    assert await project_linkage_registry.list_linked_resources(
        db,
        owner=owner,
        project_id=project['id'],
    ) == []
    with pytest.raises(errors.NotFoundError, match='要挂靠的资源不存在或不属于你'):
        await project_linkage_registry.link(
            db,
            owner=owner,
            resource_uri=uri,
            project_id=project['id'],
        )


@pytest.mark.asyncio
async def test_designsystem_linkage_only_publishes_revision_after_commit(db: AsyncSession) -> None:
    """挂靠事务提交前不得污染全局 revision；提交后由写边界显式发布。"""
    try:
        await redis_client.ping()
    except Exception as exc:
        pytest.skip(f'本地 Redis 不可达，跳过: {exc!r}')

    tag = f'{id(db):x}-commit'
    owner = f'h_ds_link_{tag}'
    revision_key = (
        f'{sync_invalidate_service.REV_PREFIX}:'
        f'{sync_invalidate_service.KIND_DESIGNSYSTEM}'
    )
    project_id: str | None = None
    design_system_id: int | None = None
    try:
        project = await project_service.create_project(db, owner=owner, data={'name': f'项目 {tag}'})
        project_id = project['id']
        row = DesignSystem(
            owner_hasn_id=owner,
            name=f'设计系统 {tag}',
            slug=f'ds-link-{tag}',
            source_kind='generated',
            content_hash=f'hash-{tag}',
        )
        db.add(row)
        await db.flush()
        design_system_id = row.id
        uri = f'hasn://designsystem/{row.id}'
        await db.commit()

        # 先发布数据库当前的合法指纹（资源尚未挂靠），不能往共享 Redis 塞测试 marker。
        before = await sync_invalidate_service.bump(
            sync_invalidate_service.KIND_DESIGNSYSTEM,
            db,
        )

        linked = await project_linkage_registry.link(
            db,
            owner=owner,
            resource_uri=uri,
            project_id=project['id'],
        )
        assert linked['changed'] is True
        assert await redis_client.get(revision_key) == before

        await db.commit()
        await project_linkage_registry.bump_sync_after_commit(
            db,
            owner=owner,
            resource_uri=uri,
        )
        published = await redis_client.get(revision_key)
        expected = await sync_invalidate_service.compute_designsystem_revision(db)
        assert published == expected
        assert published != before
    finally:
        await db.rollback()
        if design_system_id is not None:
            await db.execute(sa.delete(DesignSystem).where(DesignSystem.id == design_system_id))
        if project_id is not None:
            await db.execute(sa.delete(HasnProject).where(HasnProject.id == project_id))
        await db.commit()
        await sync_invalidate_service.bump(
            sync_invalidate_service.KIND_DESIGNSYSTEM,
            db,
        )
