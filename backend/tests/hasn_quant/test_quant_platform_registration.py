"""QUANT-P2/P3 — quant（NautilusTrader 量化引擎接入，模块 14 doc23）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端 Agent 工具面（doc23 §3/§6）：
- manifest 通过 ``validate_manifest``；进 ``_builtin_manifests``；cloud-brokered 形态 5 tools[] 非空。
- 5 capabilities：mcp_name 全 hasn.quant.*、risk=low、human_confirmation.required=False；scope 混合
  （quant:read×3 / quant:write×1 / quant:backtest×1，本期全出厂 allow）。
- **每个 manifest tool.handler 都能在 gateway ``_internal_handlers()`` 注册表解析**（gateway_internal 进程内直调
  quant_tool_handlers → quant_service → quant_engine_provider → quant-engine-service，handler 缺失会 15050）。
- idempotent 仅纯读工具（quant:read）为 True；写/提交回测为 False。
- scopes.py 登记 quant:read/write/backtest（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）；出厂 default_mode=allow。
- App 形态（cloud / install_policy=manual / 量化工作台 /apps/quant / 个人模式）。
- catalog 出厂源：sort_order 75 / default_agent_type=quant_trader（量化交易官）/ 无 per-app config_json。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落库且 hash 自愈幂等；``ensure_catalog_seeded`` 幂等播种。
- **安全断言**：实盘线 scope（quant:trade/quant:deploy，出厂 ask）**不在 manifest 暴露**——本期 P0–P5 只回测，
  实盘 P6+ 受 P0-闸1 硬闸。

形态对照（与 finance/creator 一致）：quant 是 **cloud-brokered** 业务应用，故 quant scope **不进**
``DEFAULT_AGENT_SCOPES``（cloud gateway 工具由 capability_modes 三态判定，非 JWT scope claim）。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/23-NautilusTrader量化交易引擎(云服务·工具即服务)接入设计.md §3/§6/§7。
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
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway
from backend.app.hasn.service.app_catalog_service import (
    _CATALOG_AGENT_DEFAULTS,
    _CATALOG_DEFAULT_CONFIG,
    _CATALOG_SORT_ORDER,
    ensure_catalog_seeded,
    get_catalog,
)
from backend.app.hasn_quant.manifest import QUANT_AI_NATIVE_MANIFEST, build_quant_app
from backend.app.mcp.apps.quant import quant_tool_handlers
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.security.agent_jwt import DEFAULT_AGENT_SCOPES
from backend.database.db import SQLALCHEMY_DATABASE_URL

_READ = 'quant:read'
_WRITE = 'quant:write'
_BACKTEST = 'quant:backtest'

# 5 个回测线工具落地表（= manifest tools[] / handler 注册表 / quant_tool_handlers 三处必须一致）。
_TOOL_SHORT_NAMES = {
    'save_strategy',
    'list_strategies',
    'get_strategy',
    'backtest',
    'get_backtest',
}
# tool_id 短名 → 期望 scope（混合 scope，区别于 finance 全只读）。
_EXPECTED_SCOPE = {
    'save_strategy': _WRITE,
    'list_strategies': _READ,
    'get_strategy': _READ,
    'backtest': _BACKTEST,
    'get_backtest': _READ,
}
# 实盘线 scope（出厂 ask，P6+ 真钱）——必须**不**出现在本期 manifest 暴露的工具里。
_LIVE_SCOPES = {'quant:trade', 'quant:deploy'}


# ============================ 纯 Python（无 DB）============================


def test_quant_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(QUANT_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_quant_in_builtin_registry() -> None:
    """quant 进 _builtin_manifests；cloud-brokered 形态 5 tools[] 非空。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'quant' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('quant')
    assert manifest['app_id'] == 'quant'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'cloud'
    assert manifest['transport_mode'] == 'cloud'
    assert len(manifest['tools']) == 5
    assert len(manifest['capabilities']) == 5


def test_quant_capabilities_shape_and_scopes() -> None:
    """5 个 capability，mcp_name 全 hasn.quant.*；scope 混合（read/write/backtest）；全 low + 免确认。"""
    caps = QUANT_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _TOOL_SHORT_NAMES, f'工具集与落地不一致: {names ^ _TOOL_SHORT_NAMES}'
    for cap in caps:
        short = cap['tool_id'].split('.', 1)[1]
        assert cap['mcp_name'] == f'hasn.quant.{short}', cap['mcp_name']
        assert cap['required_scopes'] == [_EXPECTED_SCOPE[short]], f"{cap['tool_id']} scope 错"
        assert cap['risk_level'] == 'low'
        assert cap['human_confirmation'].get('required') is False


def test_quant_tools_transport_and_idempotency() -> None:
    """5 个 tool 全 transport=gateway_internal、handler=quant.<flat_name>；idempotent 仅纯读为 True。"""
    for tool in QUANT_AI_NATIVE_MANIFEST['tools']:
        short = tool['tool_id'].split('.', 1)[1]
        assert tool['transport'] == 'gateway_internal'
        assert tool['handler'] == f'quant.{short}', tool['handler']
        assert tool['required_scopes'] == [_EXPECTED_SCOPE[short]]
        # 纯读（quant:read）可安全重试 → idempotent；写/提交回测 → 非幂等。
        assert tool['idempotent'] is (tool['required_scopes'] == [_READ])


def test_quant_every_tool_handler_resolves_in_gateway() -> None:
    """**关键跨切**：每个 manifest tool.handler 都能在 gateway _internal_handlers() 注册表解析。

    handler 缺失会让运行时抛 15050 internal_handler_missing。这是 manifest（声明）↔ gateway 注册表
    （执行）↔ quant_tool_handlers（实现）三处零漂移的硬保证。
    """
    handlers = ai_native_runtime_gateway._internal_handlers()
    for tool in QUANT_AI_NATIVE_MANIFEST['tools']:
        key = tool['handler']
        assert key in handlers, f'gateway 注册表缺 handler: {key}'
        flat = key.split('.', 1)[1]
        assert handlers[key] is getattr(quant_tool_handlers, f'handle_{flat}')


def test_quant_live_trading_scopes_not_exposed() -> None:
    """安全：实盘线 scope（quant:trade/quant:deploy，出厂 ask）**不在** manifest 暴露的工具/能力里（本期只回测）。"""
    exposed = {s for c in QUANT_AI_NATIVE_MANIFEST['capabilities'] for s in c['required_scopes']}
    exposed |= {s for t in QUANT_AI_NATIVE_MANIFEST['tools'] for s in t['required_scopes']}
    assert exposed.isdisjoint(_LIVE_SCOPES), f'本期不得暴露实盘线 scope: {exposed & _LIVE_SCOPES}'


def test_quant_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 quant:read/write/backtest（聚合进全局 SCOPE_CATALOG）；出厂 default_mode=allow、risk=low。"""
    for key, label in (
        (_READ, '查看量化策略与回测'),
        (_BACKTEST, '发起回测'),
        (_WRITE, '保存/更新策略'),
    ):
        assert key in SCOPE_CATALOG, f'scope 未登记: {key}'
        meta = scope_meta(key)
        assert meta['domain'] == 'quant'
        assert meta['risk'] == 'low'
        assert meta['default_mode'] == 'allow'
        assert meta['label'] == label


def test_quant_live_scopes_registered_but_ask() -> None:
    """实盘线 scope 仍登记在 catalog（webui 三态展示），但出厂 default_mode=ask、risk=high（真钱强闸）。"""
    for key in _LIVE_SCOPES:
        assert key in SCOPE_CATALOG
        meta = scope_meta(key)
        assert meta['domain'] == 'quant'
        assert meta['risk'] == 'high'
        assert meta['default_mode'] == 'ask'


def test_quant_scopes_not_minted_into_jwt() -> None:
    """quant scope 不进 DEFAULT_AGENT_SCOPES（cloud-brokered 工具由 capability_modes 判定，非 JWT claim；同 finance/creator）。"""
    for key in (_READ, _WRITE, _BACKTEST, *_LIVE_SCOPES):
        assert key not in DEFAULT_AGENT_SCOPES, f'cloud-brokered scope 不应铸入 JWT: {key}'


def test_quant_notifications_emit_declared() -> None:
    """quant 声明 notifications.emit（display_name=量化交易）。"""
    emit = QUANT_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['card_message'] is True
    assert emit['display_name'] == '量化交易'


def test_quant_workbench_app_shape() -> None:
    """App：cloud + 手动安装 + 个人模式 + 量化工作台 /apps/quant（非新窗口）。"""
    app = build_quant_app()
    assert app.id == 'quant'
    assert app.execution_mode == 'cloud'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/quant'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(QUANT_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert QUANT_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_quant_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order 75 / quant_trader 默认承接 / 无 per-app config_json。"""
    assert _CATALOG_SORT_ORDER['quant'] == 75
    assert _CATALOG_AGENT_DEFAULTS['quant'][0] == 'quant_trader'
    assert _CATALOG_AGENT_DEFAULTS['quant'][1]  # 业务提示词非空
    # quant 无平台级模型配置（引擎地址/令牌走云端 env QUANT_ENGINE_*，不进 catalog config_json）。
    assert 'quant' not in _CATALOG_DEFAULT_CONFIG


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
async def test_quant_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 quant manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'quant')
    assert published['app_id'] == 'quant'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(QUANT_AI_NATIVE_MANIFEST)

    again = await ai_native_app_registry.ensure_builtin_published(db, 'quant')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_quant_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 quant catalog 行（重复跑不重复插），关键字段与出厂源一致。"""
    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='quant')
    assert cat is not None, 'quant catalog 行未播种'
    assert cat.app_id == 'quant'
    assert cat.name == '量化交易'
    assert cat.execution_mode == 'cloud'
    assert cat.entry_route == '/apps/quant'
    assert cat.default_agent_type == 'quant_trader'
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'

    # 二次播种：不重复插（quant 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'quant')
        )
    ).scalar()
    assert cats == 1, f'quant catalog 行应唯一，实际 {cats}'
