"""CU-P4 — computer_use（分身 GUI 桌面控制 · Computer Use，模块 23 V2）AI-Native 平台接入 真实测试（零 mock）。

覆盖云端注册（catalog/manifest 注册 + 铸六 scope，设计 §3.1/§3.3）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（16 个；截屏观察 3 → computer_use:capture、全屏 1 →
  computer_use:capture_screen、控制 9 → computer_use:control、启动 1 → computer_use:launch_app、
  强退 1 → computer_use:kill_app、浏览器 1 → computer_use:browser；mcp_name 全 hasn.computer.*）。
- 出厂默认三态（= crates/hasn-mcp/src/computer/tools.rs 本地出厂态）：
  capture/list_apps/wait/scroll/launch_app 出厂 Allow（human_confirmation.required=False）；
  capture_screen + 其余 8 个控制动作 + kill_app/page 出厂 Ask（human_confirmation.required=True）。
- scope 出厂 default_mode：capture=allow、launch_app=allow、capture_screen=ask、control=ask、kill_app=ask、browser=ask
  （scroll 的工具粒度 Allow 是 control scope 内例外——control scope 默认仍 ask，落在工具 human_confirmation 上）。
- scopes.py 登记六 scope（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）+ 域标签 computer_use。
- App 形态（local_tool / 手动安装 / 内联路由 /apps/computer-use；能力型应用按需装）。
- catalog 出厂源：sort_order=80 / default_agent_type=assistant（全能助理·非专有分身）/ 无 config_json
  （driver 引擎随桌面端下发 + TCC 授权，属 CU-P2b 驱动桥，不在 catalog config）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等；
  ``ensure_catalog_seeded`` 幂等播种 computer_use catalog 行（重复跑不重复插）。

事实源: docs/产品与技术/技术设计/03-产品应用/分身桌面控制/01-总体设计.md §3.1/§3.3/§4.4.1。
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
from backend.app.hasn_computer_use.manifest import COMPUTER_USE_AI_NATIVE_MANIFEST, build_computer_use_app
from backend.app.mcp.scopes import SCOPE_CATALOG, domain_label, scope_meta
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 落地真相（hasn-node crates/hasn-mcp/src/computer/tools.rs，本表是云端侧契约源）：
# 截屏观察 3 / 全屏 1 / 控制 9 / 启动 1 / 强退 1 / 浏览器 1 = 16 个。
_CAPTURE_TOOLS = {'capture', 'list_apps', 'wait'}
_CAPTURE_SCREEN_TOOLS = {'capture_screen'}
_CONTROL_TOOLS = {
    'click',
    'double_click',
    'right_click',
    'type',
    'key',
    'scroll',
    'drag',
    'set_value',
    'focus_app',
}
_LAUNCH_APP_TOOLS = {'launch_app'}
_KILL_APP_TOOLS = {'kill_app'}
_BROWSER_TOOLS = {'page'}

_CAPTURE_SCOPE = 'computer_use:capture'
_CAPTURE_SCREEN_SCOPE = 'computer_use:capture_screen'
_CONTROL_SCOPE = 'computer_use:control'
_LAUNCH_APP_SCOPE = 'computer_use:launch_app'
_KILL_APP_SCOPE = 'computer_use:kill_app'
_BROWSER_SCOPE = 'computer_use:browser'

# 每工具 → 期望 scope。
_TOOL_SCOPE = {
    **dict.fromkeys(_CAPTURE_TOOLS, _CAPTURE_SCOPE),
    **dict.fromkeys(_CAPTURE_SCREEN_TOOLS, _CAPTURE_SCREEN_SCOPE),
    **dict.fromkeys(_CONTROL_TOOLS, _CONTROL_SCOPE),
    **dict.fromkeys(_LAUNCH_APP_TOOLS, _LAUNCH_APP_SCOPE),
    **dict.fromkeys(_KILL_APP_TOOLS, _KILL_APP_SCOPE),
    **dict.fromkeys(_BROWSER_TOOLS, _BROWSER_SCOPE),
}

# 六个 computer_use 域 scope 全集。
_ALL_SCOPES = {
    _CAPTURE_SCOPE,
    _CAPTURE_SCREEN_SCOPE,
    _CONTROL_SCOPE,
    _LAUNCH_APP_SCOPE,
    _KILL_APP_SCOPE,
    _BROWSER_SCOPE,
}

# 出厂 Allow（human_confirmation.required=False）vs 出厂 Ask（True）：
# scroll 仅改视口→工具粒度 Allow（control scope 内例外）；capture/list_apps/wait 只读→Allow；
# launch_app 后台拉起不抢焦点、非破坏（低风险）→Allow；capture_screen + 其余 8 个控制动作 + kill_app/page 均→Ask。
_ALLOW_TOOLS = _CAPTURE_TOOLS | {'scroll', 'launch_app'}
_ASK_TOOLS = (set(_TOOL_SCOPE) - _ALLOW_TOOLS)


# ============================ 纯 Python（无 DB）============================


def test_computer_use_manifest_validates() -> None:
    """manifest 通过 validate_manifest（workbench_app 已注册，scope/协作模式一致）。"""
    result = ai_native_app_registry.validate_manifest(COMPUTER_USE_AI_NATIVE_MANIFEST)
    assert result.valid, f'manifest 校验失败: {result.errors}'
    assert result.manifest_hash.startswith('sha256:')


def test_computer_use_in_builtin_registry() -> None:
    """computer_use 进 _builtin_manifests，可经 get_builtin_manifest 取回。"""
    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'computer_use' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('computer_use')
    assert manifest['app_id'] == 'computer_use'
    assert manifest['version'] == '1.0.0'
    assert manifest['execution_mode'] == 'local_tool'
    # 方案 A：本地工具不进 tools[]。
    assert manifest['tools'] == []


def test_computer_use_capabilities_match_landed_tools() -> None:
    """16 个 capability，mcp_name 全 hasn.computer.*；每工具 scope + 出厂确认态与本地工具逐一对齐。"""
    caps = COMPUTER_USE_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == set(_TOOL_SCOPE), f'工具集与落地不一致: {names}'
    assert len(caps) == 16

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.computer.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        assert cap['required_scopes'] == [_TOOL_SCOPE[short]], f'{short} scope 应为 {_TOOL_SCOPE[short]}'
        # 出厂 Allow（截屏观察 + scroll）不弹确认；出厂 Ask（全屏 + 其余控制动作）弹确认。
        expected_confirm = short in _ASK_TOOLS
        assert cap['human_confirmation'].get('required') is expected_confirm, f'{short} 确认态不符'


def test_computer_use_management_scopes_are_six_domain_scopes() -> None:
    """非 :read required_scopes 集合 = 六个 computer_use 域 scope（capture 不以 :read 结尾亦计入）。"""
    management = {
        scope
        for cap in COMPUTER_USE_AI_NATIVE_MANIFEST['capabilities']
        for scope in (cap.get('required_scopes') or [])
        if not scope.endswith(':read')
    }
    assert management == _ALL_SCOPES


def test_computer_use_scopes_registered_in_catalog() -> None:
    """scopes.py 登记六 scope（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）+ 域标签。"""
    for scope in _ALL_SCOPES:
        assert scope in SCOPE_CATALOG, f'{scope} 未聚合进 SCOPE_CATALOG'
        assert scope_meta(scope)['domain'] == 'computer_use'
    assert scope_meta(_CAPTURE_SCOPE)['label'] == '截取窗口与观察屏幕'
    assert scope_meta(_CAPTURE_SCREEN_SCOPE)['label'] == '全屏截图'
    assert scope_meta(_CONTROL_SCOPE)['label'] == '控制桌面（点击/输入/拖拽）'
    assert scope_meta(_LAUNCH_APP_SCOPE)['label'] == '启动/打开桌面 App'
    assert scope_meta(_KILL_APP_SCOPE)['label'] == '关闭/强退桌面 App'
    assert scope_meta(_BROWSER_SCOPE)['label'] == '浏览器页面自动化'
    # 域标签（DOMAIN_LABELS 单一事实源，零漂移守卫覆盖）。
    assert domain_label('computer_use', 'zh') == '桌面控制'
    assert domain_label('computer_use', 'en') == 'Computer Use'


def test_computer_use_scope_factory_defaults_match_local_enforcement() -> None:
    """出厂 default_mode 成为唯一真相（= computer/tools.rs 本地出厂态）：
    capture 出厂 Allow（窗口级只读观察）；capture_screen 出厂 Ask（全屏可能框进隐私）；
    control 出厂 Ask（真实点击/输入有副作用；scroll 的工具粒度 Allow 是 scope 内例外，
    落在工具 human_confirmation 上、不改 scope 默认）。
    """
    assert scope_meta(_CAPTURE_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_CAPTURE_SCREEN_SCOPE)['default_mode'] == 'ask'
    assert scope_meta(_CONTROL_SCOPE)['default_mode'] == 'ask'
    # launch_app 低风险出厂 Allow（后台拉起、不抢焦点、非破坏）；kill_app/browser 出厂 Ask（强退破坏性/浏览器自动化）。
    assert scope_meta(_LAUNCH_APP_SCOPE)['default_mode'] == 'allow'
    assert scope_meta(_KILL_APP_SCOPE)['default_mode'] == 'ask'
    assert scope_meta(_BROWSER_SCOPE)['default_mode'] == 'ask'
    # risk 展示：capture=low / capture_screen=medium / control=high。
    assert scope_meta(_CAPTURE_SCOPE)['risk'] == 'low'
    assert scope_meta(_CAPTURE_SCREEN_SCOPE)['risk'] == 'medium'
    assert scope_meta(_CONTROL_SCOPE)['risk'] == 'high'
    # launch_app=low（不抢焦点、非破坏）/ kill_app=high（破坏性）/ browser=high。
    assert scope_meta(_LAUNCH_APP_SCOPE)['risk'] == 'low'
    assert scope_meta(_KILL_APP_SCOPE)['risk'] == 'high'
    assert scope_meta(_BROWSER_SCOPE)['risk'] == 'high'


def test_computer_use_notifications_emit_declared() -> None:
    """computer_use 声明 notifications.emit（完成/派发卡摊给主人发卡）。"""
    emit = COMPUTER_USE_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '桌面控制'


def test_computer_use_workbench_app_shape() -> None:
    """App：local_tool + 手动安装（能力型应用按需装）+ 内联路由 /apps/computer-use（非新窗口）。"""
    app = build_computer_use_app()
    assert app.id == 'computer_use'
    assert app.name == '桌面控制'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/apps/computer-use'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(COMPUTER_USE_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert COMPUTER_USE_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


def test_computer_use_catalog_factory_source() -> None:
    """catalog 出厂源（app_catalog_service）：sort_order=80 / assistant 默认承接 / 无 config_json。"""
    assert _CATALOG_SORT_ORDER['computer_use'] == 80
    assert _CATALOG_AGENT_DEFAULTS['computer_use'][0] == 'assistant'
    assert _CATALOG_AGENT_DEFAULTS['computer_use'][1]  # 业务提示词非空
    # driver 引擎随桌面端下发 + TCC 授权（CU-P2b 驱动桥），不在 catalog config_json。
    assert 'computer_use' not in _CATALOG_DEFAULT_CONFIG


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
async def test_computer_use_builtin_published_and_self_heals(db: AsyncSession) -> None:
    """ensure_builtin_published 落 computer_use manifest 行；hash 与代码 manifest 一致；二次调用幂等。"""
    published = await ai_native_app_registry.ensure_builtin_published(db, 'computer_use')
    assert published['app_id'] == 'computer_use'
    assert published['status'] == 'published'
    assert published['manifest_hash'] == _manifest_hash(COMPUTER_USE_AI_NATIVE_MANIFEST)

    # 二次调用：hash 未变 → 直接返回已发布（不新增/不报错）。
    again = await ai_native_app_registry.ensure_builtin_published(db, 'computer_use')
    assert again['manifest_hash'] == published['manifest_hash']


@pytest.mark.asyncio
async def test_computer_use_catalog_seeded_idempotent(db: AsyncSession) -> None:
    """ensure_catalog_seeded 幂等播种 computer_use catalog 行（重复跑不重复插），关键字段与出厂源一致。

    先在事务内删掉可能存在的 computer_use 行再播种：ensure_catalog_seeded 是**只插缺失、不改
    既有**（line 388 幂等语义）——dev DB 若残留早期增量编辑窗口播下的旧行（default_agent_type=None），
    只插不改就永远不自愈。本测要验证的是**当前代码**播出的行形态，故先删后播、断言后回滚（不污染 dev DB）。
    """
    # 清掉本事务内可见的既有行（回滚会撤销，dev DB 不变）。
    await db.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id == 'computer_use'))
    await db.flush()

    await ensure_catalog_seeded(db)
    cat = await get_catalog(db, app_id='computer_use')
    assert cat is not None, 'computer_use catalog 行未播种'
    assert cat.app_id == 'computer_use'
    assert cat.name == '桌面控制'
    assert cat.execution_mode == 'local_tool'
    assert cat.entry_route == '/apps/computer-use'
    assert cat.default_agent_type == 'assistant'  # 全能助理承接（_CATALOG_AGENT_DEFAULTS）
    assert cat.work_session_system_prompt  # 业务提示词随行播下
    assert cat.default_mount is False  # install_policy=manual → 不自动挂载
    assert cat.access_type == 'free'
    assert cat.config_json == {}  # driver 引擎不入 catalog config（CU-P2b 驱动桥另下发）

    # 二次播种：不重复插（computer_use 行仍唯一）。
    await ensure_catalog_seeded(db)
    cats = (
        await db.execute(
            sa.select(sa.func.count()).select_from(HasnAppCatalog).where(HasnAppCatalog.app_id == 'computer_use')
        )
    ).scalar()
    assert cats == 1, f'computer_use catalog 行应唯一，实际 {cats}'
