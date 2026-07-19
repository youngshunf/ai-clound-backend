"""finance AI-Native 平台接入真实测试（零 mock）。

覆盖金融投研最终本地契约：
- manifest 通过 ``validate_manifest`` 并进入内置注册表，执行形态为 ``local_tool/local``。
- 云端 ``tools[]`` 与 ``capabilities[]`` 为空；最终 44 个 ``hasn.finance.*`` 工具由 hasn-node 与 Hub 维护。
- 旧 14 个云端只读 handler 在 F8 退役前仍可解析，但不再进入新工具发现面。
- scopes.py 登记 finance:read（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）；出厂 default_mode=allow。
- App 形态（local_tool / install_policy=manual / 金融投研 /apps/finance）。
- catalog 出厂源：sort_order 70 / default_agent_type=analyst（投研分析师）/ 无 per-app config_json。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落库且 hash 自愈幂等；``ensure_catalog_seeded`` 幂等播种。

事实源：docs/hasn-node设计文档/金融投研与量化交易/实施/03-全功能收口施工总纲(基于2026-07-19实现审计).md。
"""

from __future__ import annotations

from pathlib import Path
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
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_catalog_service import (
    _CATALOG_AGENT_DEFAULTS,
    _CATALOG_DEFAULT_CONFIG,
    _CATALOG_SORT_ORDER,
    catalog_to_manifest,
    ensure_catalog_seeded,
    get_catalog,
)
from backend.app.hasn_finance.manifest import FINANCE_AI_NATIVE_MANIFEST, build_finance_app
from backend.app.hasn_finance.service import finance_tool_handlers
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.database.db import SQLALCHEMY_DATABASE_URL

_READ_SCOPE = 'finance:read'
_CATALOG_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / 'sql'
    / 'hasn'
    / 'migrations'
    / '2026-07-19-finance-local-project-contract.sql'
)
# F8 退役前保留的旧云端 handler 集合；它们不得重新进入 manifest 或工具说明。
_TOOL_SHORT_NAMES = {
    'stock_quote_history',
    'stock_realtime',
    'stock_info',
    'stock_fund_flow',
    'stock_billboard',
    'stock_financial',
    'hk_quote_history',
    'us_quote_history',
    'index_quote_history',
    'fund_nav_history',
    'fund_position',
    'futures_quote_history',
    'macro_indicator',
    'bond_quote_history',
}


# ============================ 纯 Python（无 DB）============================


def test_finance_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(FINANCE_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')

    invalid = dict(FINANCE_AI_NATIVE_MANIFEST)
    invalid['project_aware'] = False
    invalid['project_required'] = True
    invalid_result = ai_native_app_registry.validate_manifest(invalid)
    assert not invalid_result.valid
    assert 'project_required_requires_project_aware' in invalid_result.errors


def test_finance_in_builtin_registry() -> None:
    """finance 进内置注册表；本地工具形态不在云端重复暴露旧工具。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'finance' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('finance')
    assert manifest['app_id'] == 'finance'
    assert manifest['version'] == '2.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    assert manifest['transport_mode'] == 'local'
    assert manifest['tools'] == []
    assert manifest['capabilities'] == []


def test_legacy_finance_handlers_remain_reachable_until_retirement() -> None:
    """F8 前旧云端只读 handler 仍可服务旧客户端，但不再进入新 manifest 工具面。"""
    handlers = ai_native_runtime_gateway._internal_handlers()
    for name in _TOOL_SHORT_NAMES:
        key = f'finance.{name}'
        assert key in handlers, f'gateway 注册表缺 handler: {key}'
        assert handlers[key] is getattr(finance_tool_handlers, f'handle_{name}')


def test_finance_project_participation_contract() -> None:
    """finance 可选参与项目：支持归集，但不选项目也能完整使用。"""
    assert FINANCE_AI_NATIVE_MANIFEST['project_aware'] is True
    assert FINANCE_AI_NATIVE_MANIFEST['project_required'] is False
    app_manifest = build_finance_app().to_manifest()
    assert app_manifest['project_aware'] is True
    assert app_manifest['project_required'] is False


def test_finance_scope_registered_in_catalog() -> None:
    """scopes.py 登记 finance:read（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）；出厂 allow。"""
    assert _READ_SCOPE in SCOPE_CATALOG
    meta = scope_meta(_READ_SCOPE)
    assert meta['domain'] == 'finance'
    assert meta['risk'] == 'low'
    assert meta['default_mode'] == 'allow'
    assert meta['label'] == '查询行情与投研数据'


def test_finance_notifications_emit_declared() -> None:
    """finance 声明 notifications.emit（display_name=金融投研）。"""
    emit = FINANCE_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['card_message'] is True
    assert emit['display_name'] == '金融投研'


def test_finance_workbench_app_shape() -> None:
    """App：local_tool + 手动安装 + 金融投研 /apps/finance（非新窗口）。"""
    app = build_finance_app()
    assert app.id == 'finance'
    assert app.execution_mode == 'local_tool'
    assert app.project_aware is True
    assert app.project_required is False
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'workspace_shared'
    assert app.scope == ('personal', 'enterprise')
    assert app.entry_route == '/apps/finance'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(FINANCE_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert FINANCE_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_finance_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order 70 / analyst 默认承接 / 无 per-app config_json。"""
    assert _CATALOG_SORT_ORDER['finance'] == 70
    assert _CATALOG_AGENT_DEFAULTS['finance'][0] == 'analyst'
    assert _CATALOG_AGENT_DEFAULTS['finance'][1]  # 业务提示词非空
    # finance 的本地引擎配置由 daemon 管理，不在云端 catalog 重复保存。
    assert 'finance' not in _CATALOG_DEFAULT_CONFIG


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
async def test_finance_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 finance manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'finance')
    assert published['app_id'] == 'finance'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(FINANCE_AI_NATIVE_MANIFEST)

    again = await ai_native_app_registry.ensure_builtin_published(db, 'finance')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_finance_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 finance catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='finance')
    assert cat is not None, 'finance catalog 行未播种'
    assert cat.app_id == 'finance'
    assert cat.entry_route == '/apps/finance'
    assert cat.default_agent_type == 'analyst'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'
    projected = catalog_to_manifest(cat, registry_app=build_finance_app())
    assert projected['execution_mode'] == 'local_tool'
    assert projected['project_aware'] is True
    assert projected['project_required'] is False

    # 二次播种：不重复插（finance 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'finance')
        )
    ).scalar()
    assert cats == 1, f'finance catalog 行应唯一，实际 {cats}'


@pytest.mark.asyncio
async def test_finance_catalog_migration_updates_existing_row(db: AsyncSession) -> None:
    """目录迁移把存量 finance 行切换为金融投研本地执行形态。"""
    await ensure_catalog_seeded(db)
    sql = _CATALOG_MIGRATION.read_text(encoding='utf-8')
    connection = await db.connection()
    await connection.exec_driver_sql(sql)
    db.expire_all()

    cat = await get_catalog(db, app_id='finance')
    assert cat is not None
    assert cat.name == '金融投研'
    assert cat.description == build_finance_app().description
    assert cat.execution_mode == 'local_tool'
