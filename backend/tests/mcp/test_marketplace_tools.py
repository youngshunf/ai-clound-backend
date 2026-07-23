"""marketplace MCP 工具单测（15-技能市场/11-doc）——真实 service，禁 mock。

覆盖：
- 静态契约：9 工具的 name/scope/execution_location/source/descriptor 合法。
- 注册：server 注册全部 marketplace 工具。
- 维度②（真实 DB，无 DB 跳过不伪造）：
  - get_skill 不存在 → found=False（不静默造数据）。
  - install_skill 目标不可达（agent/技能不存在）→ reachable=False + reason，不静默成功。
  - search_skills/search_templates 返回结构化结果（空库也成立）。
- publish 入参守卫（无需 DB）：base64 缺失/非法/空 → RequestError。

活体 DB：DATABASE_PORT=15432 pytest backend/tests/mcp/test_marketplace_tools.py
"""

from __future__ import annotations

import base64

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.marketplace import (
    MARKETPLACE_TOOLS,
    GetSkillTool,
    InstallSkillTool,
    PublishSkillTool,
    SearchSkillsTool,
    SearchTemplatesTool,
    _require_owner_hasn_id,
)
from backend.common.exception import errors


def _agent_ctx() -> AgentContext:
    return AgentContext(
        hasn_id='a_marketplace_tool_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_marketplace_tool_test',
        session_uuid='amk_marketplace_tool_test',
    )


def test_marketplace_tools_require_owner_identity() -> None:
    """凭证缺少主人身份时必须在访问市场写服务前拒绝。"""
    context = AgentContext(
        hasn_id='a_marketplace_missing_owner',
        owner_id=1,
        agent_status='active',
        metadata={},
    )

    with pytest.raises(RuntimeError, match='owner_hasn_id'):
        _require_owner_hasn_id(context)


async def _db_reachable() -> bool:
    try:
        import sqlalchemy as sa

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(sa.text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ─────────────────────────── 静态契约 ───────────────────────────


def test_marketplace_tools_count_and_names() -> None:
    """3 组 10 个云端工具，命名 hasn.marketplace.<verb>（package_* 在 hasn-mcp 本地，不在此；
    publish_skill_pack 为 B2 技能包载体收编新增）。"""
    names = {cls().name for cls in MARKETPLACE_TOOLS}
    assert names == {
        'hasn.marketplace.search_skills',
        'hasn.marketplace.get_skill',
        'hasn.marketplace.search_templates',
        'hasn.marketplace.get_template',
        'hasn.marketplace.list_installed',
        'hasn.marketplace.install_skill',
        'hasn.marketplace.uninstall_skill',
        'hasn.marketplace.publish_skill',
        'hasn.marketplace.publish_skill_pack',
        'hasn.marketplace.publish_template',
    }


def test_marketplace_tools_scopes_by_group() -> None:
    """scope 词表与 11-doc §3 对齐：read / install / publish 三档。"""
    scope_of = {cls().name: cls().required_scopes for cls in MARKETPLACE_TOOLS}
    for read_tool in (
        'hasn.marketplace.search_skills',
        'hasn.marketplace.get_skill',
        'hasn.marketplace.search_templates',
        'hasn.marketplace.get_template',
        'hasn.marketplace.list_installed',
    ):
        assert scope_of[read_tool] == ['marketplace:read']
    assert scope_of['hasn.marketplace.install_skill'] == ['marketplace:install']
    assert scope_of['hasn.marketplace.uninstall_skill'] == ['marketplace:install']
    assert scope_of['hasn.marketplace.publish_skill'] == ['marketplace:publish']
    assert scope_of['hasn.marketplace.publish_template'] == ['marketplace:publish']


def test_marketplace_tools_are_cloud_platform() -> None:
    """11-doc §4：本文件 9 工具均 source=platform、execution_location=cloud（打包才是 local）。"""
    for cls in MARKETPLACE_TOOLS:
        tool = cls()
        assert tool.source == 'platform'
        assert tool.execution_location == 'cloud'
        assert tool.namespace == 'hasn.marketplace'


def test_marketplace_tools_descriptors_valid() -> None:
    """descriptor() 能解析 canonical 名 + 暴露 execution_location/scopes/risk。"""
    for cls in MARKETPLACE_TOOLS:
        desc = cls().descriptor()
        assert desc['canonical_name'].startswith('hasn.marketplace.')
        assert desc['source'] == 'platform'
        assert desc['execution_location'] == 'cloud'
        assert desc['risk_level'] in {'low', 'medium', 'high'}


def test_scope_catalog_has_marketplace_scopes() -> None:
    """scopes.py catalog 登记 marketplace 三 scope（喂 13-doc 权限 tab）。"""
    from backend.app.mcp.scopes import SCOPE_CATALOG

    for scope in ('marketplace:read', 'marketplace:install', 'marketplace:publish'):
        assert scope in SCOPE_CATALOG
        assert SCOPE_CATALOG[scope]['domain'] == 'marketplace'


def test_server_registers_marketplace_tools() -> None:
    """server._register_builtin_tools 注册全部 marketplace 工具。"""
    from backend.app.mcp.server import mcp_server

    registered = {tool.name for tool in mcp_server.tool_registry.get_all_tools()}
    for cls in MARKETPLACE_TOOLS:
        assert cls().name in registered, f'{cls().name} 未注册'


# ─────────────────────────── publish 入参守卫（无需 DB）───────────────────────────


@pytest.mark.asyncio
async def test_publish_skill_rejects_missing_base64() -> None:
    """publish 缺 package_base64 → RequestError（在落库前就拦，零 fake）。"""
    with pytest.raises(errors.RequestError):
        await PublishSkillTool().execute(_agent_ctx(), {})


@pytest.mark.asyncio
async def test_publish_skill_rejects_invalid_base64() -> None:
    """非法 base64 → RequestError。"""
    with pytest.raises(errors.RequestError):
        await PublishSkillTool().execute(_agent_ctx(), {'package_base64': '!!!not-base64!!!'})


@pytest.mark.asyncio
async def test_publish_skill_rejects_empty_payload() -> None:
    """合法 base64 但解码后为空 → RequestError。"""
    empty_b64 = base64.b64encode(b'').decode()
    with pytest.raises(errors.RequestError):
        await PublishSkillTool().execute(_agent_ctx(), {'package_base64': empty_b64})


# ─────────────────────────── 维度②（真实 DB）───────────────────────────


@pytest.mark.asyncio
async def test_get_skill_nonexistent_returns_found_false() -> None:
    """维度②：不存在/未发布技能 → found=False，不静默造数据。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    result = await GetSkillTool().execute(_agent_ctx(), {'skill_id': 'user/h_nope/does-not-exist-xyz'})
    assert result['found'] is False
    assert result.get('reason')


@pytest.mark.asyncio
async def test_install_skill_unreachable_returns_reachable_false() -> None:
    """维度②：目标 agent/技能不存在 → reachable=False + reason，不静默成功（零 mock）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    result = await InstallSkillTool().execute(_agent_ctx(), {'skill_id': 'user/h_nope/does-not-exist-xyz'})
    assert result['installed'] is False
    assert result['reachable'] is False
    assert result.get('reason')


@pytest.mark.asyncio
async def test_search_skills_returns_structured_results() -> None:
    """search_skills 返回结构化结果（空库也成立：results 为 list、total 为 int）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    result = await SearchSkillsTool().execute(_agent_ctx(), {'q': 'zzz_no_such_skill_zzz', 'limit': 5})
    assert isinstance(result['results'], list)
    assert isinstance(result['total'], int)


@pytest.mark.asyncio
async def test_search_templates_returns_structured_results() -> None:
    """search_templates 返回结构化结果。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    result = await SearchTemplatesTool().execute(_agent_ctx(), {'q': 'zzz_no_such_template_zzz', 'limit': 5})
    assert isinstance(result['results'], list)
    assert isinstance(result['total'], int)
