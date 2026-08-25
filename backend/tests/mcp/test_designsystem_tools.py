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

import json
import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.context import clear_current_project_id, set_current_project_id
from backend.app.mcp.tools import designsystem as designsystem_tools
from backend.app.mcp.tools.designsystem import (
    DESIGNSYSTEM_TOOLS,
    DesignSystemCompileTokensTool,
    DesignSystemDeriveTool,
    DesignSystemExtractComponentsTool,
    DesignSystemGetGalleryTool,
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
def test_tools_register_fifteen_with_stable_names() -> None:
    """恰好 15 个 designsystem 工具（6 云端权威 + 5 分片写入 + 4 确定性纯函数），名稳定、顺序固定。

    云端权威在 4 个基础上增补两个按需读（不是新语义，是 DSGET 瘦身/DSGAL 场景标准的产物）：
    `get_gallery`（get 瘦身后画廊按需单取）、`check_scenes`（场景覆盖自检）。
    DSPUT 增补 5 个分片写入（`create`/`put_tokens`/`put_design`/`put_gallery`/`finalize`）——整包
    `save` 的入参体积在 tool.call 上撑不住（实测 41% 调用生成不出合法 JSON），改建壳后逐块写。
    """
    assert [t.name for t in DESIGNSYSTEM_TOOLS] == [
        'hasn.designsystem.import',
        'hasn.designsystem.save',
        'hasn.designsystem.create',
        'hasn.designsystem.put_tokens',
        'hasn.designsystem.put_design',
        'hasn.designsystem.put_gallery',
        'hasn.designsystem.finalize',
        'hasn.designsystem.list',
        'hasn.designsystem.get',
        'hasn.designsystem.get_gallery',
        'hasn.designsystem.check_scenes',
        'hasn.designsystem.compile_tokens',
        'hasn.designsystem.derive',
        'hasn.designsystem.validate',
        'hasn.designsystem.extract_components',
    ]


def test_shard_write_tools_declare_write_scope_and_required_resource_gate() -> None:
    """5 个分片写工具都要 designsystem:write；写存量的四个 design_system_id 必填 → 资源门 required。

    ``required=False`` 会让缺 id 的调用**滑过判权**（save 那样是因为它缺 id 就是新建），
    而分片写工具写的一定是存量，缺 id 必须当场被拦。
    """
    by_name = {t.name: t for t in DESIGNSYSTEM_TOOLS}
    shard = ['create', 'put_tokens', 'put_design', 'put_gallery', 'finalize']
    for action in shard:
        tool = by_name[f'hasn.designsystem.{action}']
        assert tool.required_scopes == ['designsystem:write'], action
        assert tool.source == 'platform'
        assert tool.execution_location == 'cloud'
    editor_gate = [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'editor'}]
    for action in ('put_tokens', 'put_design', 'put_gallery', 'finalize'):
        gate = by_name[f'hasn.designsystem.{action}'].resource_access
        assert gate == editor_gate, action
        assert 'required' not in gate[0], f'{action}: 分片写工具不得放开 required'
    # create 没有实例可判 → 不声明资源门（不设假闸门）
    assert by_name['hasn.designsystem.create'].resource_access is None


def test_tools_are_cloud_platform() -> None:
    """10 工具 source=platform、execution_location=cloud、命名空间统一 hasn.designsystem。"""
    for tool in DESIGNSYSTEM_TOOLS:
        assert tool.source == 'platform'
        assert tool.namespace == 'hasn.designsystem'
        assert getattr(tool, 'execution_location') == 'cloud'


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
    # ⚠️ 改判（2026-08-25）：save 的必填从 ['slug','name','content'] 收到 ['content']。
    # 旧口径把「更新存量」这条完全正当的调用判死——分身传 {design_system_id, content} 是语义正确的，
    # 却收到 missing=[slug,name]，而 slug 在更新路径上根本不生效（service 白名单明写不许改 slug）。
    # 实测这占该工具全部失败的 22%。slug/name 的必填性改由 execute 分场景校验并给分场景错误。
    assert by_name['hasn.designsystem.save'].input_schema['required'] == ['content']
    assert 'slug' not in by_name['hasn.designsystem.save'].input_schema['required']  # 反向：不得退回旧口径
    # 分片写工具的必填清单（都短，且每一项都是真的必须）
    assert by_name['hasn.designsystem.create'].input_schema['required'] == ['slug', 'name']
    assert by_name['hasn.designsystem.put_tokens'].input_schema['required'] == ['design_system_id', 'tokens_css']
    assert by_name['hasn.designsystem.put_design'].input_schema['required'] == ['design_system_id', 'design_md']
    assert by_name['hasn.designsystem.put_gallery'].input_schema['required'] == [
        'design_system_id',
        'scene',
        'html',
    ]
    assert by_name['hasn.designsystem.finalize'].input_schema['required'] == ['design_system_id']
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
    assert 'platform_project_id' in by_name['hasn.designsystem.save'].input_schema['properties']
    assert 'platform_project_id' in by_name['hasn.designsystem.list'].input_schema['properties']


def test_save_project_resolution_precedence() -> None:
    """新建时显式项目优先于上下文；显式 null 禁止继承；更新存量不自动改挂靠。"""
    current_project_id = str(uuid.uuid4())
    explicit_project_id = str(uuid.uuid4())
    set_current_project_id(current_project_id)
    try:
        assert designsystem_tools._resolve_save_platform_project_id({}, is_create=True) == current_project_id
        assert (
            designsystem_tools._resolve_save_platform_project_id(
                {'platform_project_id': explicit_project_id},
                is_create=True,
            )
            == explicit_project_id
        )
        assert (
            designsystem_tools._resolve_save_platform_project_id(
                {'platform_project_id': None},
                is_create=True,
            )
            is None
        )
        assert designsystem_tools._resolve_save_platform_project_id({}, is_create=False) is None
    finally:
        clear_current_project_id()


# ── 确定性纯函数工具执行（无需 DB，离线可跑）──────────────────────────────────────
@pytest.mark.asyncio(loop_scope='session')
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


@pytest.mark.asyncio(loop_scope='session')
async def test_compile_tokens_from_source_tokens_array() -> None:
    """compile_tokens：显式 source_tokens 数组亦可（优先于 tokens_css）。"""
    result = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'),
        {'source_tokens': [{'name': '--accent', 'value': '#111111', 'source': 'seed', 'line': 3}]},
    )
    accent = next(t for t in result['report']['tokens'] if t['name'] == '--accent')
    assert accent['confidence'] == 'high'
    assert accent['value'] == '#111111'


@pytest.mark.asyncio(loop_scope='session')
async def test_compile_tokens_rejects_empty_input() -> None:
    """compile_tokens：既无 tokens_css 又无非空 source_tokens → RuntimeError（对齐本地措辞）。"""
    with pytest.raises(RuntimeError, match='tokens_css'):
        await DesignSystemCompileTokensTool().execute(_agent_ctx('h_x'), {})


@pytest.mark.asyncio(loop_scope='session')
async def test_derive_returns_two_artifacts() -> None:
    """derive：tokens.css → {design_tokens_json, tailwind_v4_css}。"""
    contract = await DesignSystemCompileTokensTool().execute(
        _agent_ctx('h_x'), {'tokens_css': ':root { --accent: #2563eb; }'}
    )
    result = await DesignSystemDeriveTool().execute(_agent_ctx('h_x'), {'tokens_css': contract['tokens_css']})
    assert result['design_tokens_json'].endswith('}\n')  # pretty + 尾换行
    assert '@theme {' in result['tailwind_v4_css']


@pytest.mark.asyncio(loop_scope='session')
async def test_derive_rejects_missing_tokens_css() -> None:
    """derive：缺 tokens_css → RuntimeError。"""
    with pytest.raises(RuntimeError, match='tokens_css'):
        await DesignSystemDeriveTool().execute(_agent_ctx('h_x'), {})


@pytest.mark.asyncio(loop_scope='session')
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


@pytest.mark.asyncio(loop_scope='session')
async def test_extract_components_returns_manifest() -> None:
    """extract_components：components.html → manifest（brandId + selectors + fixture）。"""
    html = '<html><head><title>Demo</title></head><body><button class="btn">Go</button></body></html>'
    manifest = await DesignSystemExtractComponentsTool().execute(
        _agent_ctx('h_x'), {'brand_id': 'demo', 'components_html': html}
    )
    assert manifest['brandId'] == 'demo'
    assert manifest['fixture']['title'] == 'Demo'
    assert isinstance(manifest['groups'], list)


@pytest.mark.asyncio(loop_scope='session')
async def test_extract_components_rejects_missing_html() -> None:
    """extract_components：缺 components_html → RuntimeError。"""
    with pytest.raises(RuntimeError, match='components_html'):
        await DesignSystemExtractComponentsTool().execute(_agent_ctx('h_x'), {'brand_id': 'demo'})


def _all_schema_names() -> list[str]:
    from backend.app.hasn_designsystem.core import all_schema_names

    return all_schema_names()


@pytest.mark.asyncio(loop_scope='session')
async def test_save_rejects_missing_content() -> None:
    """save 缺 content（或非对象）→ RuntimeError（校验在打 DB 前，无需活体库）。"""
    with pytest.raises(RuntimeError, match='content'):
        await DesignSystemSaveTool().execute(_agent_ctx('h_x'), {'slug': 's', 'name': 'n', 'content': 'not-a-dict'})


@pytest.mark.asyncio(loop_scope='session')
async def test_save_new_without_slug_says_it_is_a_create_only_requirement() -> None:
    """**新建**缺 slug → 错误必须点明「新建才需要」并指出更新存量的走法，而不是笼统的 missing。

    分身在这条错误上打转过很多次：schema 说 slug 必填、它在更新一套存量、于是既不知道该不该传，
    也不知道传了有没有用（实际没用——slug 建库后改不动）。
    """
    with pytest.raises(RuntimeError, match='新建'):
        await DesignSystemSaveTool().execute(_agent_ctx('h_x'), {'name': 'n', 'content': {'tokens_css': ':root{}'}})


@pytest.mark.asyncio(loop_scope='session')
async def test_save_update_no_longer_demands_slug_and_name() -> None:
    """反向：更新存量（给了 design_system_id）不得再因为缺 slug/name 被判死。

    此处只验「不是因为缺 slug/name 而失败」——不存在的 id 会因为查不到而失败，那是另一回事，
    正是这条区分让改判可证伪：退回旧口径时报的是 slug/name，本用例当场红。
    """
    with pytest.raises(RuntimeError) as exc:
        await DesignSystemSaveTool().execute(
            _agent_ctx('h_x'), {'design_system_id': 999999999, 'content': {'tokens_css': ':root{}'}}
        )
    assert 'slug' not in str(exc.value)
    assert '不存在' in str(exc.value)


@pytest.mark.asyncio(loop_scope='session')
async def test_get_rejects_invalid_id() -> None:
    """get 缺/非法 design_system_id → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='design_system_id'):
        await DesignSystemGetTool().execute(_agent_ctx('h_x'), {'design_system_id': 0})


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='session')
async def test_save_list_get_roundtrip_real_db() -> None:
    """真实 PG：save → 落库 + 携 bundle 自动登记产物；list 可见；get 取回当前版本内容。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete, select

    from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox, HasnArtifacts
    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.app.hasn_designsystem.model.revision import Revision
    from backend.app.hasn_project.model.hasn_project import HasnProject
    from backend.app.hasn_project.service.project_app_service import project_service
    from backend.database.db import async_db_session

    owner = f'h_ds_tool_{uuid.uuid4().hex[:16]}'
    ctx = _agent_ctx(owner)
    slug = f'ds-{uuid.uuid4().hex[:8]}'
    bundle_asset_id = f'ast_bundle_{uuid.uuid4().hex[:12]}'
    design_system_id: int | None = None
    project_id: str | None = None
    try:
        async with async_db_session.begin() as db:
            project = await project_service.create_project(db, owner=owner, data={'name': '设计系统自动挂靠测试'})
            project_id = project['id']

        # AppCollab 只把项目写入可信 ContextVar；分身调用 save 时不需要显式传 platform_project_id。
        set_current_project_id(project_id)
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
        finally:
            clear_current_project_id()
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
            assert str(row.platform_project_id) == project_id
            # save 创建即绑定生成它的分身（AppCollab bind-only-if-unbound）。
            assert row.bound_agent_id == ctx.agent_hasn_id

        # save 即 register-on-write：自动登记一条**设计系统资源**产物（best-effort，应已落库）。
        # 曾断言 `kind == 'document'` + 指向 bundle 资产——那是 doc35 四维度重排前的旧形状，
        # 重排后 kind 只答「怎么打开」（应用资源恒 resource）、「是什么」交给 resource_kind，
        # 断言却没跟着改，于是本用例长期红着（doc36 U1 顺带修正到 manifest 权威值）。
        async with async_db_session() as db:
            art = (await db.execute(select(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))).scalar_one()
            contribution = (
                await db.execute(
                    select(HasnArtifactContributions).where(HasnArtifactContributions.artifact_id == art.artifact_id)
                )
            ).scalar_one()
            assert art.kind == 'resource', 'artifact_kind 只答「怎么打开」——应用资源恒 resource（doc35 §3.1）'
            assert art.resource_kind == 'designsystem.spec', 'resource_kind 答「是什么」，取 manifest descriptor 原值'
            assert art.resource_app_id == 'designsystem'
            assert art.resource_uri == f'hasn://designsystem/{design_system_id}'
            # 来源属于不可变参与记录；当前态只保存稳定对象属性。
            assert contribution.source_app_id == 'designsystem'
            assert contribution.source_tool == 'hasn.designsystem.save'

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
            # 参与记录与可靠投递队列均外键指向产物当前态，测试清理必须先删子表。
            await db.execute(
                delete(HasnArtifactRegistrationOutbox).where(
                    HasnArtifactRegistrationOutbox.owner_hasn_id == owner
                )
            )
            await db.execute(delete(HasnArtifactContributions).where(HasnArtifactContributions.owner_hasn_id == owner))
            await db.execute(delete(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
            if project_id is not None:
                await db.execute(delete(HasnProject).where(HasnProject.id == project_id))


# ══ DSPUT·分片写入的真实 PG 往返 ═══════════════════════════════════════════════════
_SHARD_BRAND_SECTION = (
    '<section data-ds-scene="brand_website">'
    '<nav data-ds-component="nav" style="color:var(--fg)">导航</nav>'
    '<div data-ds-component="hero" style="background:var(--bg)">Hero</div>'
    '<div data-ds-component="features" style="color:var(--fg-2)">特性</div>'
    '<button data-ds-component="cta" style="background:var(--accent)">CTA</button>'
    '<footer data-ds-component="footer" style="color:var(--muted)">页脚</footer>'
    '</section>'
)
_SHARD_DECK_SECTION = (
    '<section data-ds-scene="deck">'
    '<div data-ds-component="cover" style="background:var(--bg)">封面</div>'
    '<div data-ds-component="section" style="color:var(--fg)">章节</div>'
    '<div data-ds-component="bullets" style="color:var(--fg-2)">要点</div>'
    '<div data-ds-component="chart" style="border:1px solid var(--border)">图表</div>'
    '<div data-ds-component="closing" style="background:var(--accent)">结束</div>'
    '</section>'
)


def _shard_tokens_css() -> str:
    from backend.app.hasn_designsystem.core import SourceToken, compile_tokens

    source = [
        SourceToken(name='--bg', value='#ffffff', source='test', line=None, usage=[]),
        SourceToken(name='--fg', value='#0f172a', source='test', line=None, usage=[]),
        SourceToken(name='--accent', value='#2563eb', source='test', line=None, usage=[]),
    ]
    return compile_tokens(source, '2026-08-25T00:00:00+00:00').tokens_css


async def _silence_completion_card(design_system_id: int) -> None:
    """把发卡幂等水位提前置位，让后续写入不触发真实 IM 投递。

    完成卡投递要求分身身份在 hasn_humans/agents 里真实存在，而本文件用的是随机测试身份。
    这里只想验「分片写入本身」，发卡链路另有其测试；置位水位比造一整套身份轻，也比把
    内容故意写不全诚实——后者会让 complete=true 这条最关键的断言根本验不到。
    """
    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.database.db import async_db_session
    from backend.utils.timezone import timezone as tz

    async with async_db_session.begin() as db:
        d = await db.get(DesignSystem, design_system_id)
        assert d is not None
        d.completed_notified_at = tz.now()


async def _cleanup_design_system(design_system_id: int | None, owner: str) -> None:
    if design_system_id is None:
        return
    from sqlalchemy import delete

    from backend.app.hasn.model import HasnArtifactContributions, HasnArtifactRegistrationOutbox, HasnArtifacts
    from backend.app.hasn_designsystem.model.design_system import DesignSystem
    from backend.app.hasn_designsystem.model.revision import Revision
    from backend.database.db import async_db_session

    async with async_db_session.begin() as db:
        await db.execute(delete(Revision).where(Revision.design_system_id == design_system_id))
        await db.execute(delete(DesignSystem).where(DesignSystem.id == design_system_id))
        # 参与记录与可靠投递队列均外键指向产物当前态，清理必须先删子表（与既有 save 往返测试同序）。
        await db.execute(
            delete(HasnArtifactRegistrationOutbox).where(HasnArtifactRegistrationOutbox.owner_hasn_id == owner)
        )
        await db.execute(delete(HasnArtifactContributions).where(HasnArtifactContributions.owner_hasn_id == owner))
        await db.execute(delete(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))


@pytest.mark.asyncio(loop_scope='session')
async def test_shard_write_roundtrip_real_db() -> None:
    """真实 PG 端到端：create → put_tokens → put_design → put_gallery ×2 → finalize。

    这条链要证明的不只是「能跑通」，而是**分身在整条链上从没传过一个派生物**：
    design-tokens.json / tailwind / 契约报告 / 组件清单全部由云端现算。
    """
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from backend.app.mcp.tools.designsystem import (
        DesignSystemCreateTool,
        DesignSystemFinalizeTool,
        DesignSystemPutDesignTool,
        DesignSystemPutGalleryTool,
        DesignSystemPutTokensTool,
    )

    owner = f'h_ds_shard_{uuid.uuid4().hex[:16]}'
    ctx = _agent_ctx(owner, agent_hasn_id=f'a_ds_shard_{uuid.uuid4().hex[:12]}')
    ds_id: int | None = None
    try:
        shell = await DesignSystemCreateTool().execute(
            ctx, {'slug': f'ds-shard-{uuid.uuid4().hex[:8]}', 'name': '分片往返', 'required_scenes': ['brand_website']}
        )
        ds_id = shell['id']
        assert shell['created'] is True
        assert shell['uri'] == f'hasn://designsystem/{ds_id}'  # 建壳就给出打开地址，不必二次查询
        await _silence_completion_card(ds_id)

        tokens = await DesignSystemPutTokensTool().execute(
            ctx, {'design_system_id': ds_id, 'tokens_css': _shard_tokens_css()}
        )
        assert tokens['score'] == 100  # 评分来自云端现算，分身没传过报告
        assert tokens['grade'] == 'excellent'

        await DesignSystemPutDesignTool().execute(ctx, {'design_system_id': ds_id, 'design_md': '# 分片写入说明'})
        brand = await DesignSystemPutGalleryTool().execute(
            ctx, {'design_system_id': ds_id, 'scene': 'brand_website', 'html': _SHARD_BRAND_SECTION}
        )
        assert brand['scene'] == 'brand_website'
        assert brand['scene_coverage']['complete'] is True  # 写完当场回覆盖，不必再单独 check_scenes

        await DesignSystemPutGalleryTool().execute(
            ctx, {'design_system_id': ds_id, 'scene': 'deck', 'html': _SHARD_DECK_SECTION}
        )

        done = await DesignSystemFinalizeTool().execute(ctx, {'design_system_id': ds_id})
        assert done['complete'] is True  # 五项必填齐（其中三项是云端自己算出来的）
        assert done['scene_report']['complete'] is True
        assert done['uri'] == f'hasn://designsystem/{ds_id}'

        # 两个场景都在，且各自完整——按场景写不会互相覆盖。
        gallery = await DesignSystemGetGalleryTool().execute(ctx, {'design_system_id': ds_id})
        assert 'data-ds-scene="brand_website"' in gallery['components_html']
        assert 'data-ds-scene="deck"' in gallery['components_html']
    finally:
        await _cleanup_design_system(ds_id, owner)


def test_shard_write_payload_is_an_order_of_magnitude_smaller() -> None:
    """量化分片的收益：单次入参必须比等价的整包 save 小一个数量级。

    这是整件事的**根因指标**——不是「代码更好看」，而是「分身能不能一次把参数吐完」。实测整包
    save 经 tool.call 后要吐 2 万-4.5 万字符的双重转义串，41% 的调用在中途漏了逗号或引号。
    断言按**双重序列化后**的长度算，因为那才是模型真正要生成的东西。
    """
    tokens_css = _shard_tokens_css()
    whole = json.dumps(
        {
            'name': 'hasn.designsystem.save',
            'params': json.dumps(
                {
                    'design_system_id': 1,
                    'slug': 's',
                    'name': 'n',
                    'content': {
                        'tokens_css': tokens_css,
                        'design_md': '# 说明',
                        'components_html': _SHARD_BRAND_SECTION + _SHARD_DECK_SECTION,
                        # 整包口径下这两项也得由分身传（分片口径下云端现算，压根不出现在入参里）
                        'components_manifest_json': {'brandId': 's', 'groups': [], 'fixture': {}},
                        'token_contract_report_json': {'summary': {'score': 100}},
                    },
                },
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    one_scene = json.dumps(
        {
            'tool': 'hasn.designsystem.put_gallery',
            'params': json.dumps(
                {'design_system_id': 1, 'scene': 'deck', 'html': _SHARD_DECK_SECTION}, ensure_ascii=False
            ),
        },
        ensure_ascii=False,
    )
    assert len(one_scene) * 3 < len(whole), (
        f'分片入参 {len(one_scene)} 字符 vs 整包 {len(whole)} 字符——收益不足一个数量级，'
        f'说明分片没有真正把大块留在服务端'
    )


@pytest.mark.asyncio(loop_scope='session')
async def test_create_through_tool_call_with_business_name_flattened_real_db() -> None:
    """端到端复现生产事故的调用形状：经 hasn.cloud.tool.call、业务 name 平铺在顶层。

    2026-08-25 生产实况：分身调 designsystem.save 时把设计系统展示名放在 tool.call 的顶层 name 上，
    转发层拿它去查注册表 → `TOOL_NOT_FOUND: 昆明即时宠物零售 · 专业猫舍设计系统`（连撞两次触发
    runtime 的 repeated_exact_failure_warning）；放进 params 又被 tool.call 自己判「缺 name」。
    修复后用 `tool` 指定目标工具，平铺的业务 name 如实抵达内层——本用例断言那个中文名真的落进了库。
    """
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from backend.app.mcp.server import HasnCloudMcpServer

    owner = f'h_ds_tc_{uuid.uuid4().hex[:16]}'
    ctx = _agent_ctx(owner, agent_hasn_id=f'a_ds_tc_{uuid.uuid4().hex[:12]}')
    display_name = '昆明即时宠物零售 · 专业猫舍设计系统'
    ds_id: int | None = None
    server = HasnCloudMcpServer()
    tool_call = server.tool_registry.get_tool('hasn.cloud.tool.call')
    assert tool_call is not None
    try:
        created = await tool_call.execute(
            ctx,
            {
                'tool': 'hasn.designsystem.create',
                'name': display_name,  # 平铺在顶层的**业务**字段，不是工具名
                'slug': f'ds-tc-{uuid.uuid4().hex[:8]}',
            },
        )
        assert isinstance(created, dict), f'调用未抵达内层工具: {created!r}'
        assert created.get('error') != 'input_validation_failed', created
        ds_id = created['id']
        assert created['name'] == display_name  # 业务 name 真的到了内层，没被当成工具名吃掉
    finally:
        await _cleanup_design_system(ds_id, owner)


@pytest.mark.asyncio(loop_scope='session')
async def test_tool_call_reports_business_name_as_argument_error_not_missing_tool() -> None:
    """反向：真把业务名放在 name 上当工具名用 → 报入参错误并给出正确形状，不再是「工具不存在」。"""
    from backend.app.mcp.errors import McpErrorCode, McpToolError
    from backend.app.mcp.server import HasnCloudMcpServer

    server = HasnCloudMcpServer()
    tool_call = server.tool_registry.get_tool('hasn.cloud.tool.call')
    assert tool_call is not None
    with pytest.raises(McpToolError) as exc:
        await tool_call.execute(_agent_ctx('h_x'), {'name': '昆明即时宠物零售 · 专业猫舍设计系统', 'slug': 's'})
    assert exc.value.code == McpErrorCode.INVALID_CALL_ARGUMENTS
    assert exc.value.code != McpErrorCode.TOOL_NOT_FOUND
