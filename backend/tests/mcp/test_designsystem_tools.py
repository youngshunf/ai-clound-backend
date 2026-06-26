"""平台工具 · designsystem 域 真实 service 测试（禁 mock，TOOLMIG-4）。

验证从 hasn-node 本地 hasn-mcp 迁来的 4 个云端权威工具：
- `hasn.designsystem.import` / `.save`（写类，designsystem:write）
- `hasn.designsystem.list` / `.get`（读类，无 scope）

契约（无需 DB）：工具名/命名空间/execution_location/scope 与原 hasn-mcp 工具 1:1；
input_schema 必填项 + 入参校验防回归。
真实 PG 往返：save 真落 hasn_designsystem 表 + 落一版 revision + 携 bundle 时自动登记产物；
list 可见该套；get 取回当前版本内容。测试后清理该 owner 行（含 revision/artifact）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_designsystem_tools.py
无 DB 时跳过真实往返（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.designsystem import (
    DESIGNSYSTEM_TOOLS,
    DesignSystemGetTool,
    DesignSystemListTool,
    DesignSystemSaveTool,
)


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_designsystem_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        scopes=[],
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_designsystem_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ── 契约（无需 DB）────────────────────────────────────────────────────────────
def test_tools_register_four_with_stable_names() -> None:
    """恰好 4 个云端工具，名稳定（与本地保留的 4 个纯函数工具分离）。"""
    assert [t.name for t in DESIGNSYSTEM_TOOLS] == [
        'hasn.designsystem.import',
        'hasn.designsystem.save',
        'hasn.designsystem.list',
        'hasn.designsystem.get',
    ]


def test_tools_are_cloud_platform() -> None:
    """4 工具 source=platform、execution_location=cloud、命名空间统一 hasn.designsystem。"""
    for tool in DESIGNSYSTEM_TOOLS:
        assert tool.source == 'platform'
        assert tool.namespace == 'hasn.designsystem'
        assert tool.execution_location == 'cloud'


def test_write_tools_declare_scope_read_tools_do_not() -> None:
    """写类 import/save → designsystem:write；读类 list/get 无 scope（与本地 1:1）。"""
    by_name = {t.name: t for t in DESIGNSYSTEM_TOOLS}
    assert by_name['hasn.designsystem.import'].required_scopes == ['designsystem:write']
    assert by_name['hasn.designsystem.save'].required_scopes == ['designsystem:write']
    assert by_name['hasn.designsystem.list'].required_scopes == []
    assert by_name['hasn.designsystem.get'].required_scopes == []


def test_required_fields_match_contract() -> None:
    """必填项与原 hasn-mcp 工具逐字段一致。"""
    by_name = {t.name: t for t in DESIGNSYSTEM_TOOLS}
    assert by_name['hasn.designsystem.import'].input_schema['required'] == ['source', 'ref']
    assert by_name['hasn.designsystem.save'].input_schema['required'] == ['slug', 'name', 'content']
    assert by_name['hasn.designsystem.get'].input_schema['required'] == ['design_system_id']
    assert 'required' not in by_name['hasn.designsystem.list'].input_schema


@pytest.mark.asyncio(loop_scope='module')
async def test_save_rejects_missing_content() -> None:
    """save 缺 content（或非对象）→ RuntimeError（校验在打 DB 前，无需活体库）。"""
    with pytest.raises(RuntimeError, match='content'):
        await DesignSystemSaveTool().execute(
            _agent_ctx('h_x'), {'slug': 's', 'name': 'n', 'content': 'not-a-dict'}
        )


@pytest.mark.asyncio(loop_scope='module')
async def test_save_rejects_missing_slug() -> None:
    """save 缺 slug → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='slug'):
        await DesignSystemSaveTool().execute(_agent_ctx('h_x'), {'name': 'n', 'content': {}})


@pytest.mark.asyncio(loop_scope='module')
async def test_get_rejects_invalid_id() -> None:
    """get 缺/非法 design_system_id → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='design_system_id'):
        await DesignSystemGetTool().execute(_agent_ctx('h_x'), {'design_system_id': 0})


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_save_list_get_roundtrip_real_db() -> None:
    """真实 PG：save → 落库 + 携 bundle 自动登记产物；list 可见；get 取回当前版本内容。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete, select

    from backend.app.hasn.model import HasnArtifacts
    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.app.hasn_designsystem.model.revision import Revision
    from backend.database.db import async_db_session

    owner = f'h_ds_tool_{uuid.uuid4().hex[:16]}'
    ctx = _agent_ctx(owner)
    slug = f'ds-{uuid.uuid4().hex[:8]}'
    bundle_asset_id = f'ast_bundle_{uuid.uuid4().hex[:12]}'
    design_system_id: int | None = None
    try:
        saved = await DesignSystemSaveTool().execute(
            ctx,
            {
                'slug': slug,
                'name': '唤星品牌系统',
                'content': {
                    'tokens_css': ':root { --color-bg-base: #ffffff; --color-accent-default: #6d28d9; }',
                    'design_tokens_json': '{}',
                },
                'category': 'brand',
                'score': 88,
                'grade': 'excellent',
                'bundle_asset_id': bundle_asset_id,
            },
        )
        design_system_id = saved['id']
        assert design_system_id
        assert saved['revision']['bundle_asset_id'] == bundle_asset_id

        # 落库核实：DesignSystem 行 + 一版 revision。
        async with async_db_session() as db:
            row = (
                await db.execute(select(DesignSystem).where(DesignSystem.id == design_system_id))
            ).scalar_one()
            assert row.owner_hasn_id == owner
            assert row.slug == slug
            assert row.score == 88
            # save 创建即绑定生成它的分身（AppCollab bind-only-if-unbound）。
            assert row.bound_agent_id == ctx.agent_hasn_id

        # 携 bundle → 自动登记一条 document 产物指向 bundle 资产（best-effort，应已落库）。
        async with async_db_session() as db:
            art = (
                await db.execute(
                    select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner)
                )
            ).scalar_one()
            assert art.kind == 'document'
            assert art.asset_id == bundle_asset_id
            assert art.source_tool == 'hasn.designsystem.save'

        # list 可见该套（owner 维度）。
        listed = await DesignSystemListTool().execute(ctx, {})
        assert any(item['id'] == design_system_id for item in listed['items'])

        # get 取回当前版本内容（tokens_css）。
        detail = await DesignSystemGetTool().execute(ctx, {'design_system_id': design_system_id})
        assert detail['id'] == design_system_id
        assert detail['current_revision']['tokens_css'].startswith(':root')
    finally:
        async with async_db_session.begin() as db:
            if design_system_id is not None:
                await db.execute(delete(Revision).where(Revision.design_system_id == design_system_id))
                await db.execute(delete(DesignSystem).where(DesignSystem.id == design_system_id))
            await db.execute(delete(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
