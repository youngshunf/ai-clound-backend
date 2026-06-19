"""DS-P7 — designsystem（自研设计系统生成应用，模块 14/20）AI-Native 平台接入 真实测试（零 mock）。

覆盖 P7 第一刀（catalog/manifest 注册 + 铸 scope，doc12 P7）：
- manifest 通过 ``validate_manifest``（含 workbench_app 一致性闸门）；进 ``_builtin_manifests``。
- capabilities scope 与落地 hasn-mcp 工具一致（写类 import/save → designsystem:write；读类 + 确定性纯函数
  compile_tokens/derive/validate/extract_components/list/get 无 scope；8 个；mcp_name 全 hasn.designsystem.*）。
- scopes.py 登记 designsystem:write/:publish（聚合进全局 SCOPE_CATALOG 供三态权限 UI 中文化）。
- **铸 scope**：designsystem:write/:publish 进 DEFAULT_AGENT_SCOPES（JWT scopes claim 唯一固定来源），
  且 Agent JWT 编解码忠实携带二者——否则 Agent 调云端 designsystem/agent/* 写类被 check_scopes 403。
- WorkbenchApp 形态（local_tool / 手动安装 / 内联路由；本期不自动挂载，入口随 P8 webui+catalog 落地）。
- 真实 PG：``ensure_builtin_published`` 把 manifest 落 ``hasn_ai_native_app_manifest`` 且 hash 自愈幂等。

事实源: docs/hasn-node设计文档/14-AI-Native应用平台/20-设计系统生成应用(自研)架构设计.md §7；
        docs/hasn-node设计文档/14-AI-Native应用平台/实施/12-设计系统生成应用实施清单.md P7。
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
from backend.app.hasn_designsystem.manifest import (
    DESIGNSYSTEM_AI_NATIVE_MANIFEST,
    build_designsystem_workbench_app,
)
from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta
from backend.common.security.agent_jwt import (
    DEFAULT_AGENT_SCOPES,
    jwt_decode_agent,
    jwt_encode_agent,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

# 落地真相（hasn-node crates/hasn-mcp/src/designsystem.rs）：写类 2 工具 / 读类（含纯函数）6 工具。
_WRITE_TOOLS = {'import', 'save'}
_READ_TOOLS = {'compile_tokens', 'derive', 'validate', 'extract_components', 'list', 'get'}
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
    """8 个 capability，mcp_name 全 hasn.designsystem.*；写类 designsystem:write、读类无 scope（与 .rs 一致）。"""
    caps = DESIGNSYSTEM_AI_NATIVE_MANIFEST['capabilities']
    names = {c['tool_id'].split('.', 1)[1] for c in caps}
    assert names == _WRITE_TOOLS | _READ_TOOLS, f'工具集与落地不一致: {names}'
    assert len(caps) == 8

    for cap in caps:
        assert cap['mcp_name'].startswith('hasn.designsystem.'), cap['mcp_name']
        short = cap['tool_id'].split('.', 1)[1]
        if short in _WRITE_TOOLS:
            assert cap['required_scopes'] == [_WRITE_SCOPE], f'{short} 写类应为 designsystem:write'
            # 出厂全 Allow 免确认（16-doc D-v3-1）；owner 可经 capability_modes 设 ask/deny override。
            assert cap['human_confirmation'].get('required') is False
        else:
            assert cap['required_scopes'] == [], f'{short} 读类应无 required_scopes（与落地空 scope 一致）'
            assert cap['human_confirmation'].get('required') is False


def test_designsystem_scopes_registered_in_catalog() -> None:
    """scopes.py 登记 designsystem:write/:publish（聚合进全局 SCOPE_CATALOG，供三态权限 UI 中文化）。"""
    assert _WRITE_SCOPE in SCOPE_CATALOG
    assert _PUBLISH_SCOPE in SCOPE_CATALOG
    assert scope_meta(_WRITE_SCOPE)['domain'] == 'designsystem'
    assert scope_meta(_WRITE_SCOPE)['label'] == '管理设计系统'
    assert scope_meta(_PUBLISH_SCOPE)['label'] == '发布/分享设计系统'


def test_designsystem_scopes_minted_into_agent_jwt() -> None:
    """铸 scope：designsystem:write/:publish 进 DEFAULT_AGENT_SCOPES，且 Agent JWT 编解码忠实携带二者。

    生产真相：JWT scopes claim 的唯一固定来源是 DEFAULT_AGENT_SCOPES（agent_jwt.py）。Agent 调云端
    designsystem/agent/* 写类时 check_scopes 据此放行——故二者必须在该常量内，并经真实 encode/decode 存活。
    """
    assert _WRITE_SCOPE in DEFAULT_AGENT_SCOPES
    assert _PUBLISH_SCOPE in DEFAULT_AGENT_SCOPES

    # 用 DEFAULT_AGENT_SCOPES 铸一个 Agent JWT（真实 jwt_encode_agent/jwt_decode_agent，离线无 Redis），
    # 解出的 scopes 必携带二者（= check_scopes 放行的前置）。
    expire = timezone.now() + timedelta(seconds=3600)
    payload = {
        'sub': 'a_designsystem_expert',
        'token_type': 'agent',
        'agent_hasn_id': 'a_designsystem_expert',
        'agent_name': '设计系统专家',
        'owner_hasn_id': 'h_test_owner',
        'owner_user_id': 1,
        'scopes': list(DEFAULT_AGENT_SCOPES),
        'session_uuid': str(uuid.uuid4()),
        'exp': timezone.to_utc(expire).timestamp(),
    }
    decoded = jwt_decode_agent(jwt_encode_agent(payload))
    assert _WRITE_SCOPE in decoded.scopes
    assert _PUBLISH_SCOPE in decoded.scopes


def test_designsystem_notifications_emit_declared() -> None:
    """designsystem 声明 notifications.emit（生成完成/分享发卡，[20] §7 / P10）。"""
    emit = DESIGNSYSTEM_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '设计系统'


def test_designsystem_workbench_app_shape() -> None:
    """WorkbenchApp：local_tool + 手动安装（本期不自动挂载）+ 内联路由（非新窗口）。"""
    app = build_designsystem_workbench_app()
    assert app.id == 'designsystem'
    assert app.execution_mode == 'local_tool'
    assert app.install_policy == 'manual'
    assert app.collaboration_mode == 'none'
    assert app.scope == ('personal',)
    assert app.entry_route == '/workbench/apps/designsystem'
    assert app.ui_kind is None
    # manifest.workspace_scope 必须 ⊆ workbench_app.scope（validate_manifest 闸门）。
    assert set(DESIGNSYSTEM_AI_NATIVE_MANIFEST['workspace_scope']) <= set(app.scope)
    assert DESIGNSYSTEM_AI_NATIVE_MANIFEST['collaboration_mode'] == app.collaboration_mode


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
