"""平台工具 · designsystem 域 真实 service 测试（禁 mock，TOOLMIG-4 + TOOLMIG 纯函数上云）。

验证 10 个云端 designsystem 工具：
- **云端权威（操作云端数据）**：`import` / `save`（写类，designsystem:write）、`list` / `get` /
  `get_gallery` / `check_scenes`（读类，无 scope）。
- **确定性纯函数（TOOLMIG：Python 移植 hasn_designsystem_core，云端分身可用）**：
  `compile_tokens` / `derive` / `validate` / `extract_components`（读类，无 scope，无 DB/无网络）。

契约（无需 DB）：工具名/命名空间/execution_location/scope 与本地 hasn-mcp（Rust）工具 1:1；
input_schema 必填项 + 入参校验防回归。纯函数工具直接执行核对返回形状（离线可跑）。
真实 PG 往返：save 真落 hasn_designsystem 表 + 落一版 revision + 携 bundle 时自动登记产物；
list 可见该套；get 取回当前版本内容。测试后清理该 owner 行（含 revision/artifact）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_designsystem_tools.py
无 DB 时跳过真实往返（不伪造）。纯函数工具与契约测试无需 DB。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.designsystem import (
    DESIGNSYSTEM_TOOLS,
    DesignSystemCompileTokensTool,
    DesignSystemDeriveTool,
    DesignSystemExtractComponentsTool,
    DesignSystemGetTool,
    DesignSystemListTool,
    DesignSystemSaveTool,
    DesignSystemValidateTool,
)


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_designsystem_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
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
def test_tools_register_ten_with_stable_names() -> None:
    """恰好 10 个 designsystem 工具（6 云端权威 + 4 确定性纯函数），名稳定、顺序固定。

    云端权威在 4 个基础上增补两个按需读（不是新语义，是 DSGET 瘦身/DSGAL 场景标准的产物）：
    `get_gallery`（get 瘦身后画廊按需单取）、`check_scenes`（场景覆盖自检）。
    """
    assert [t.name for t in DESIGNSYSTEM_TOOLS] == [
        'hasn.designsystem.import',
        'hasn.designsystem.save',
        'hasn.designsystem.list',
        'hasn.designsystem.get',
        'hasn.designsystem.get_gallery',
        'hasn.designsystem.check_scenes',
        'hasn.designsystem.compile_tokens',
        'hasn.designsystem.derive',
        'hasn.designsystem.validate',
        'hasn.designsystem.extract_components',
    ]


def test_tools_are_cloud_platform() -> None:
    """10 工具 source=platform、execution_location=cloud、命名空间统一 hasn.designsystem。"""
    for tool in DESIGNSYSTEM_TOOLS:
        assert tool.source == 'platform'
        assert tool.namespace == 'hasn.designsystem'
        assert tool.execution_location == 'cloud'


def test_write_tools_declare_scope_read_and_pure_tools_do_not() -> None:
    """写类 import/save → designsystem:write；读类 list/get/get_gallery/check_scenes + 4 纯函数无 scope（与本地 1:1）。"""
    by_name = {t.name: t for t in DESIGNSYSTEM_TOOLS}
    assert by_name['hasn.designsystem.import'].required_scopes == ['designsystem:write']
    assert by_name['hasn.designsystem.save'].required_scopes == ['designsystem:write']
    for read_only in (
        'hasn.designsystem.list',
        'hasn.designsystem.get',
        'hasn.designsystem.get_gallery',
        'hasn.designsystem.check_scenes',
        'hasn.designsystem.compile_tokens',
        'hasn.designsystem.derive',
        'hasn.designsystem.validate',
        'hasn.designsystem.extract_components',
    ):
        assert by_name[read_only].required_scopes == [], read_only


def test_required_fields_match_contract() -> None:
    """必填项与本地 hasn-mcp（Rust）工具逐字段一致。"""
    by_name = {t.name: t for t in DESIGNSYSTEM_TOOLS}
    assert by_name['hasn.designsystem.import'].input_schema['required'] == ['source', 'ref']
    assert by_name['hasn.designsystem.save'].input_schema['required'] == ['slug', 'name', 'content']
    assert by_name['hasn.designsystem.get'].input_schema['required'] == ['design_system_id']
    assert 'required' not in by_name['hasn.designsystem.list'].input_schema
    # 纯函数工具必填项 1:1（compile_tokens 无必填，两入参二选一在 execute 里校验）。
    assert 'required' not in by_name['hasn.designsystem.compile_tokens'].input_schema
    assert by_name['hasn.designsystem.derive'].input_schema['required'] == ['tokens_css']
    assert by_name['hasn.designsystem.validate'].input_schema['required'] == ['tokens_css']
    assert by_name['hasn.designsystem.extract_components'].input_schema['required'] == [
        'brand_id',
        'components_html',
    ]


# ── 确定性纯函数工具执行（无需 DB，离线可跑）──────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_compile_tokens_from_tokens_css() -> None:
    """compile_tokens：由 tokens_css 编译 → {tokens_css, report}，report 带 56 token 摘要。"""
    result = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'), {'tokens_css': ':root { --accent: #2563eb; }'}
    )
    assert result['tokens_css'].startswith(':root')
    assert result['report']['summary']['totalTokens'] == 56
    # --accent 精确命中 → high。
    accent = next(t for t in result['report']['tokens'] if t['name'] == '--accent')
    assert accent['confidence'] == 'high'
    assert accent['value'] == '#2563eb'


@pytest.mark.asyncio(loop_scope='module')
async def test_compile_tokens_from_source_tokens_array() -> None:
    """compile_tokens：显式 source_tokens 数组亦可（优先于 tokens_css）。"""
    result = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'),
        {'source_tokens': [{'name': '--accent', 'value': '#111111', 'source': 'seed', 'line': 3}]},
    )
    accent = next(t for t in result['report']['tokens'] if t['name'] == '--accent')
    assert accent['confidence'] == 'high'
    assert accent['value'] == '#111111'


@pytest.mark.asyncio(loop_scope='module')
async def test_compile_tokens_rejects_empty_input() -> None:
    """compile_tokens：既无 tokens_css 又无非空 source_tokens → RuntimeError（对齐本地措辞）。"""
    with pytest.raises(RuntimeError, match='tokens_css'):
        await DesignSystemCompileTokensTool().execute(_agent_ctx('h_x'), {})


@pytest.mark.asyncio(loop_scope='module')
async def test_derive_returns_two_artifacts() -> None:
    """derive：tokens.css → {design_tokens_json, tailwind_v4_css}。"""
    contract = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'), {'tokens_css': ':root { --accent: #2563eb; }'}
    )
    result = await DesignSystemDeriveTool().execute(_agent_ctx('h_x'), {'tokens_css': contract['tokens_css']})
    assert result['design_tokens_json'].endswith('}\n')  # pretty + 尾换行
    assert '@theme {' in result['tailwind_v4_css']


@pytest.mark.asyncio(loop_scope='module')
async def test_derive_rejects_missing_tokens_css() -> None:
    """derive：缺 tokens_css → RuntimeError。"""
    with pytest.raises(RuntimeError, match='tokens_css'):
        await DesignSystemDeriveTool().execute(_agent_ctx('h_x'), {})


@pytest.mark.asyncio(loop_scope='module')
async def test_validate_returns_score_report() -> None:
    """validate：完整契约 → score=100/excellent；含 selfCheck。"""
    contract = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'),
        {'source_tokens': [{'name': n, 'value': '#abcdef'} for n in _all_schema_names()]},
    )
    report = await DesignSystemValidateTool().execute(_agent_ctx('h_x'), {'tokens_css': contract['tokens_css']})
    assert report['summary']['score'] == 100
    assert report['summary']['grade'] == 'excellent'
    assert report['selfCheck']['ok'] is True


@pytest.mark.asyncio(loop_scope='module')
async def test_extract_components_returns_manifest() -> None:
    """extract_components：components.html → manifest（brandId + selectors + fixture）。"""
    html = '<html><head><title>Demo</title></head><body><button class="btn">Go</button></body></html>'
    manifest = await DesignSystemExtractComponentsTool().execute(
        _agent_ctx('h_x'), {'brand_id': 'demo', 'components_html': html}
    )
    assert manifest['brandId'] == 'demo'
    assert manifest['fixture']['title'] == 'Demo'
    assert isinstance(manifest['groups'], list)


@pytest.mark.asyncio(loop_scope='module')
async def test_extract_components_rejects_missing_html() -> None:
    """extract_components：缺 components_html → RuntimeError。"""
    with pytest.raises(RuntimeError, match='components_html'):
        await DesignSystemExtractComponentsTool().execute(_agent_ctx('h_x'), {'brand_id': 'demo'})


def _all_schema_names() -> list[str]:
    from backend.app.hasn_designsystem.core import all_schema_names

    return all_schema_names()


@pytest.mark.asyncio(loop_scope='module')
async def test_save_rejects_missing_content() -> None:
    """save 缺 content（或非对象）→ RuntimeError（校验在打 DB 前，无需活体库）。"""
    with pytest.raises(RuntimeError, match='content'):
        await DesignSystemSaveTool().execute(_agent_ctx('h_x'), {'slug': 's', 'name': 'n', 'content': 'not-a-dict'})


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
        # doc36 §3.2：写工具返回体必须带 `uri`，与下面登记落库的 resource_uri 同一个地址。
        assert saved['uri'] == f'hasn://designsystem/{design_system_id}'

        # 落库核实：DesignSystem 行 + 一版 revision。
        async with async_db_session() as db:
            row = (await db.execute(select(DesignSystem).where(DesignSystem.id == design_system_id))).scalar_one()
            assert row.owner_hasn_id == owner
            assert row.slug == slug
            assert row.score == 88
            # save 创建即绑定生成它的分身（AppCollab bind-only-if-unbound）。
            assert row.bound_agent_id == ctx.agent_hasn_id

        # save 即 register-on-write：自动登记一条**设计系统资源**产物（best-effort，应已落库）。
        # 曾断言 `kind == 'document'` + 指向 bundle 资产——那是 doc35 四维度重排前的旧形状，
        # 重排后 kind 只答「怎么打开」（应用资源恒 resource）、「是什么」交给 resource_kind，
        # 断言却没跟着改，于是本用例长期红着（doc36 U1 顺带修正到 manifest 权威值）。
        async with async_db_session() as db:
            art = (await db.execute(select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))).scalar_one()
            assert art.kind == 'resource', 'artifact_kind 只答「怎么打开」——应用资源恒 resource（doc35 §3.1）'
            assert art.resource_kind == 'designsystem.spec', 'resource_kind 答「是什么」，取 manifest descriptor 原值'
            assert art.source_app_id == 'designsystem'
            assert art.resource_uri == f'hasn://designsystem/{design_system_id}'
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
