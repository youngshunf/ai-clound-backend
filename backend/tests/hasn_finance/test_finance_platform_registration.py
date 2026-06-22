"""FIN-S2 — finance（金融数据源 akshare，模块 24）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端 Agent 工具面（设计 §4/§5）：
- manifest 通过 ``validate_manifest``；进 ``_builtin_manifests``；cloud 形态 14 tools[] 非空。
- 14 capabilities 全只读 finance:read、risk=low、human_confirmation.required=False、mcp_name 全 hasn.finance.*。
- **每个 manifest tool.handler 都能在 gateway ``_internal_handlers()`` 注册表解析**（gateway_internal 进程内直调
  finance_tool_handlers → finance_provider → finance-data-service，handler 缺失会 15050）。
- scopes.py 登记 finance:read（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）；出厂 default_mode=allow。
- App 形态（cloud / install_policy=manual / 行情看板 /apps/finance）。
- catalog 出厂源：sort_order 70 / default_agent_type=analyst（投研分析师）/ 无 per-app config_json。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落库且 hash 自愈幂等；``ensure_catalog_seeded`` 幂等播种。

形态对照（与 growth/creator 一致）：finance 是**纯云端只读数据应用**，故 finance:read **不进**
``DEFAULT_AGENT_SCOPES``（cloud gateway 工具由 capability_modes 三态判定，非 JWT scope claim；只有
local_tool 应用如 reel/film 才需铸入 JWT，因 daemon 三态闸门按 claim 校验）。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/24-金融数据源(akshare)行情与投研应用接入设计.md §4/§5；
        docs/hasn-node设计文档/14-AI-Native应用平台/实施/24-金融数据源(akshare)行情与投研应用接入实施清单.md S2。
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
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_catalog_service import (
    _CATALOG_AGENT_DEFAULTS,
    _CATALOG_DEFAULT_CONFIG,
    _CATALOG_SORT_ORDER,
    ensure_catalog_seeded,
    get_catalog,
)
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn_finance.manifest import FINANCE_AI_NATIVE_MANIFEST, build_finance_app
from backend.app.hasn_finance.service import finance_tool_handlers
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.security.agent_jwt import DEFAULT_AGENT_SCOPES
from backend.database.db import SQLALCHEMY_DATABASE_URL

_READ_SCOPE = 'finance:read'
# 14 工具落地表（= manifest tools[] / handler 注册表 / 工具说明.md 三处必须一致）。
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


def test_finance_in_builtin_registry() -> None:
    """finance 进 _builtin_manifests；cloud 形态 14 tools[] 非空。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'finance' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('finance')
    assert manifest['app_id'] == 'finance'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'cloud'
    assert manifest['transport_mode'] == 'cloud'
    # 云端工具走 gateway_internal，进 tools[]（不同于 local_tool 的空 tools[]）。
    assert len(manifest['tools']) == 14
    assert len(manifest['capabilities']) == 14


def test_finance_capabilities_all_readonly() -> None:
    """14 个 capability，mcp_name 全 hasn.finance.*；全 finance:read、low、免确认（只读数据源）。"""
    caps = FINANCE_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _TOOL_SHORT_NAMES, f'工具集与落地不一致: {names ^ _TOOL_SHORT_NAMES}'
    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.finance.'), cap['mcp_name']
        assert cap['required_scopes'] == [_READ_SCOPE], f"{cap['tool_id']} 应只读 finance:read"
        assert cap['risk_level'] == 'low'
        assert cap['human_confirmation'].get('required') is False


def test_finance_tools_transport_gateway_internal() -> None:
    """14 个 tool 全 transport=gateway_internal、idempotent=True、handler=finance.<flat_name>。"""
    for tool in FINANCE_AI_NATIVE_MANIFEST['tools']:
        assert tool['transport'] == 'gateway_internal'
        assert tool['idempotent'] is True
        assert tool['handler'].startswith('finance.'), tool['handler']
        assert tool['required_scopes'] == [_READ_SCOPE]


def test_finance_every_tool_handler_resolves_in_gateway() -> None:
    """**关键跨切**：每个 manifest tool.handler 都能在 gateway _internal_handlers() 注册表解析。

    handler 缺失会让运行时抛 15050 internal_handler_missing。这是 manifest（声明）↔ gateway 注册表
    （执行）↔ finance_tool_handlers（实现）三处零漂移的硬保证。
    """
    handlers = ai_native_runtime_gateway._internal_handlers()
    for tool in FINANCE_AI_NATIVE_MANIFEST['tools']:
        key = tool['handler']
        assert key in handlers, f'gateway 注册表缺 handler: {key}'
        # 注册表值即 finance_tool_handlers 模块里对应的 handle_<flat_name>。
        flat = key.split('.', 1)[1]
        assert handlers[key] is getattr(finance_tool_handlers, f'handle_{flat}')


def test_finance_scope_registered_in_catalog() -> None:
    """scopes.py 登记 finance:read（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）；出厂 allow。"""
    assert _READ_SCOPE in SCOPE_CATALOG
    meta = scope_meta(_READ_SCOPE)
    assert meta['domain'] == 'finance'
    assert meta['risk'] == 'low'
    assert meta['default_mode'] == 'allow'
    assert meta['label'] == '查询行情与投研数据'


def test_finance_scope_not_minted_into_jwt() -> None:
    """finance:read 不进 DEFAULT_AGENT_SCOPES（cloud 工具由 capability_modes 判定，非 JWT claim；同 growth/creator）。"""
    assert _READ_SCOPE not in DEFAULT_AGENT_SCOPES


def test_finance_notifications_emit_declared() -> None:
    """finance 声明 notifications.emit（display_name=金融数据）。"""
    emit = FINANCE_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['card_message'] is True
    assert emit['display_name'] == '金融数据'


def test_finance_workbench_app_shape() -> None:
    """App：cloud + 手动安装 + 行情看板 /apps/finance（非新窗口）。"""
    app = build_finance_app()
    assert app.id == 'finance'
    assert app.execution_mode == 'cloud'
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
    # finance 无平台级模型配置（数据服务地址/令牌走云端 env，不进 catalog config_json）。
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
    assert cat.name == '金融数据'
    assert cat.execution_mode == 'cloud'
    assert cat.entry_route == '/apps/finance'
    assert cat.default_agent_type == 'analyst'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'

    # 二次播种：不重复插（finance 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'finance')
        )
    ).scalar()
    assert cats == 1, f'finance catalog 行应唯一，实际 {cats}'
