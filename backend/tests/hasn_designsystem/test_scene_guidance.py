"""DSGAL 组件画廊「场景完整度自查」——`hasn.designsystem.check_scenes` 业务核心与工具契约（零 mock）。

福仔诉求（2026-07-12）：分身产出/精修设计系统后把 owner 勾选的 required_scenes（「要求覆盖哪些场景」的
**声明**）误当「已配齐」，对主人谎报「全套齐全」，但详情页仍显示 0/N。根因是分身没有工具看到与详情页
一致的**真实覆盖度**、也不知道怎么补。check_scenes 工具就是这个自查器：交叉 required_scenes × 当前
components.html 里 data-ds-scene/data-ds-component 标记**实际检测到**的标准组件 → 逐场景「已配齐 X/Y ·
缺哪几件 + 每件应包含什么、怎么补」。

覆盖：
- 纯函数 :func:`build_scene_report`：全缺 / 部分 / 全齐 / 多场景 + optional 加分件；complete 判定；未配齐带 next_steps。
- ratchet 守卫：``SCENE_STANDARDS`` 每个 required∪optional 组件都必须在 ``COMPONENT_GUIDANCE`` 有指引
  （防止加了组件却漏写「应包含什么」→ check_scenes 出参有洞）。
- 工具契约（``DesignSystemCheckScenesTool``）：cloud / low / 无 required_scopes / resource_access viewer-可选；
  内联 dry-run（无 id）走 build_scene_report；无 id 又无 html → 明确报错（不静默）。
- 真实 PG：``design_system_service.scene_coverage_report`` by-id 现读现检测当前 components.html + ACL 判权。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_designsystem.core.scenes import SCENE_STANDARDS
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.hasn_designsystem.service.scene_guidance import (
    COMPONENT_GUIDANCE,
    NEXT_STEPS,
    build_scene_report,
)
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.designsystem import DesignSystemCheckScenesTool
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL


def _scene(html_body: str, scene_id: str) -> str:
    """包一段 <section data-ds-scene>…</section>。"""
    return f'<section data-ds-scene="{scene_id}">{html_body}</section>'


def _comp(*keys: str) -> str:
    """把若干组件 key 拼成 data-ds-component 标记块。"""
    return ''.join(f'<div data-ds-component="{k}">x</div>' for k in keys)


# ============================ 纯函数 build_scene_report ============================


def test_report_all_missing_when_no_markers() -> None:
    """一个标记都没有 → 每个要求场景全缺（0/N）、complete=False、带 next_steps + how_to_complete。"""
    report = build_scene_report(['brand_website'], '<div>无任何场景标记</div>')
    assert report['complete'] is False
    assert report['required_scenes'] == ['brand_website']
    assert len(report['scenes']) == 1
    s = report['scenes'][0]
    assert s['id'] == 'brand_website'
    assert s['label'] == '品牌网站'
    assert s['required_total'] == 5
    assert s['present_count'] == 0
    assert len(s['missing']) == 5
    assert s['complete'] is False
    # how_to_complete 列出每件缺失组件的「应包含什么」指引（来自 COMPONENT_GUIDANCE）
    assert s['how_to_complete'] is not None
    assert '导航栏' in s['how_to_complete'] and 'data-ds-component="nav"' in s['how_to_complete']
    # 未配齐 → 附补齐工作流
    assert report['next_steps'] == list(NEXT_STEPS)
    assert 'marker_convention' in report


def test_report_partial_coverage() -> None:
    """品牌网站标了 nav/hero/features、缺 cta/footer → 3/5、missing=[cta,footer]、complete=False。"""
    html = _scene(_comp('nav', 'hero', 'features'), 'brand_website')
    report = build_scene_report(['brand_website'], html)
    s = report['scenes'][0]
    assert s['present_count'] == 3
    assert [m['key'] for m in s['present']] == ['nav', 'hero', 'features']
    assert [m['key'] for m in s['missing']] == ['cta', 'footer']
    assert [m['label'] for m in s['missing']] == ['行动号召 CTA', '页脚']
    assert s['complete'] is False
    assert report['complete'] is False
    # summary 压成一行「品牌网站 3/5 · 缺 行动号召 CTA/页脚」
    assert report['summary'] == '品牌网站 3/5 · 缺 行动号召 CTA/页脚'


def test_report_complete_scene_has_no_how_to() -> None:
    """五件必须组件全标 → complete=True、missing 空、how_to_complete=None、无 next_steps、summary 报喜。"""
    html = _scene(_comp('nav', 'hero', 'features', 'cta', 'footer'), 'brand_website')
    report = build_scene_report(['brand_website'], html)
    s = report['scenes'][0]
    assert s['present_count'] == 5
    assert s['missing'] == []
    assert s['complete'] is True
    assert s['how_to_complete'] is None
    assert report['complete'] is True
    assert 'next_steps' not in report
    assert '已配齐' in report['summary']


def test_report_optional_present_tracked() -> None:
    """optional 加分件（pricing）被标 → 进 optional_present，但不影响 required 的 complete 判定。"""
    html = _scene(_comp('nav', 'hero', 'features', 'cta', 'footer', 'pricing'), 'brand_website')
    report = build_scene_report(['brand_website'], html)
    s = report['scenes'][0]
    assert s['complete'] is True  # 五必须齐即 complete
    assert [c['key'] for c in s['optional_present']] == ['pricing']


def test_report_multi_scene_mixed() -> None:
    """多要求场景：品牌网站全齐 + 移动端部分 → 顶层 complete=False，逐场景各自判定。"""
    html = _scene(_comp('nav', 'hero', 'features', 'cta', 'footer'), 'brand_website') + _scene(
        _comp('mobile_nav', 'tab_bar'), 'mobile'
    )
    report = build_scene_report(['brand_website', 'mobile'], html)
    assert [s['id'] for s in report['scenes']] == ['brand_website', 'mobile']
    bw, mob = report['scenes']
    assert bw['complete'] is True
    assert mob['complete'] is False and mob['present_count'] == 2 and mob['required_total'] == 5
    assert report['complete'] is False
    # summary 只列未配齐的移动端
    assert report['summary'].startswith('移动端 2/5 · 缺 ')


def test_report_normalizes_required_scenes() -> None:
    """required_scenes 规整：去未知/去重保序；空/非法 → 默认 [brand_website]（零 fake，不臆造场景）。"""
    html = _scene(_comp('cover'), 'deck')
    report = build_scene_report(['deck', '玄学场景', 'deck'], html)
    assert report['required_scenes'] == ['deck']
    # 空 → 默认品牌网站
    assert build_scene_report([], html)['required_scenes'] == ['brand_website']
    # 非列表 → 默认
    assert build_scene_report(None, html)['required_scenes'] == ['brand_website']


def test_report_carries_id_and_name() -> None:
    """by-id 路径传入 design_system_id/name → 回显在报告顶层（供分身/UI 定位）。"""
    report = build_scene_report(['brand_website'], '<div/>', design_system_id=655, name='Astra Pitch Dark')
    assert report['design_system_id'] == 655
    assert report['name'] == 'Astra Pitch Dark'


# ============================ ratchet：指引无洞 ============================


def test_component_guidance_covers_every_standard_component() -> None:
    """ratchet 守卫：SCENE_STANDARDS 每个 required∪optional 组件都必须在 COMPONENT_GUIDANCE 有指引。

    加了标准组件却漏写「应包含什么」→ check_scenes 的 how_to_complete 会出洞（分身看到缺件却无补齐说明）。
    本测试逼「改场景标准必同步补指引」，反向也守：不许有多余/拼错 key 的孤儿指引。
    """
    for std in SCENE_STANDARDS:
        guide = COMPONENT_GUIDANCE.get(std.id)
        assert guide is not None, f'场景 {std.id} 缺 COMPONENT_GUIDANCE 条目'
        std_keys = {c.key for c in std.required} | {c.key for c in std.optional}
        missing = std_keys - guide.keys()
        assert not missing, f'场景 {std.id} 这些组件缺补齐指引: {missing}'
        # 反向：指引里不许有场景标准之外的孤儿 key（防拼错/删组件留残指引）
        orphan = guide.keys() - std_keys
        assert not orphan, f'场景 {std.id} 指引有孤儿 key（非标准组件）: {orphan}'
    # 指引场景集合 == 标准场景集合（不多不少）
    assert COMPONENT_GUIDANCE.keys() == {s.id for s in SCENE_STANDARDS}


# ============================ 工具契约 DesignSystemCheckScenesTool ============================


def test_tool_static_contract() -> None:
    """check_scenes 工具契约：cloud / low / 无 required_scopes / resource_access viewer-可选（对齐 get 判权）。"""
    tool = DesignSystemCheckScenesTool()
    assert tool.name == 'hasn.designsystem.check_scenes'
    assert tool.execution_location == 'cloud'
    assert tool.risk_level == 'low'
    assert tool.required_scopes == []
    ra = tool.resource_access
    assert ra == [{'param': 'design_system_id', 'type': 'designsystem', 'need': 'viewer', 'required': False}]


def _agent_ctx(owner_hasn_id: str = 'h_dryrun') -> AgentContext:
    return AgentContext(
        hasn_id='a_dryrun',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
    )


@pytest.mark.asyncio
async def test_tool_inline_dry_run_no_id() -> None:
    """内联 dry-run（无 id、给 components_html）→ 不读库直接检测草稿；报告与 build_scene_report 一致。"""
    tool = DesignSystemCheckScenesTool()
    html = _scene(_comp('nav', 'hero'), 'brand_website')
    out = await tool.execute(_agent_ctx(), {'components_html': html, 'required_scenes': ['brand_website']})
    assert out['design_system_id'] is None
    assert out['scenes'][0]['present_count'] == 2
    assert out['complete'] is False


@pytest.mark.asyncio
async def test_tool_no_id_no_html_raises() -> None:
    """既无 design_system_id 又无 components_html → 明确报错（不静默返回空/假配齐）。"""
    tool = DesignSystemCheckScenesTool()
    with pytest.raises(RuntimeError):
        await tool.execute(_agent_ctx(), {})


# ============================ 真实 PG：service by-id 现读现检测 + ACL ============================


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _content(components_html: str) -> dict:
    """一版最小内容（五必填非空），components_html 由调用方给（决定场景检测结果）。"""
    return {
        'tokens_css': ':root { --bg: #101010; --accent: #2563EB; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': components_html,
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'summary': {'score': 80, 'grade': 'good', 'recommendRebuild': False}},
    }


async def _seed_identity(session: AsyncSession, tag: str) -> tuple[str, str]:
    """落库真实主人与分身身份，使完整设计系统的完成卡经过身份校验。"""
    owner = f'h_{tag}'
    agent = f'a_{tag}'
    session.add_all(
        [
            HasnHumans(
                hasn_id=owner,
                star_id=f'hsg{tag}',
                user_id=int(uuid.uuid4().hex[:15], 16),
                nickname=f'场景自查主人{tag}',
                status='active',
            ),
            HasnAgents(
                hasn_id=agent,
                star_id=f'asg{tag}',
                owner_id=owner,
                display_name=f'场景自查分身{tag}',
                agent_name=f'guidance_{tag}',
                status='active',
            ),
        ]
    )
    await session.commit()
    return owner, agent


@pytest.mark.asyncio
async def test_service_scene_coverage_report_by_id_reads_current_html(session: AsyncSession) -> None:
    """by-id：owner 分身自查 → 现读当前版本 components.html 实检覆盖（品牌网站 3/5）+ 报告带 id/name。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = await _seed_identity(session, tag)
    html = _scene(_comp('nav', 'hero', 'features'), 'brand_website')
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='待自查',
        content=_content(html),
        required_scenes=['brand_website'],
    )
    ds_id = saved['id']

    report = await design_system_service.scene_coverage_report(
        session, design_system_id=ds_id, viewer_owner_hasn_id=owner
    )
    assert report['design_system_id'] == ds_id
    assert report['name'] == '待自查'
    assert report['required_scenes'] == ['brand_website']
    s = report['scenes'][0]
    assert s['present_count'] == 3 and [m['key'] for m in s['missing']] == ['cta', 'footer']
    assert report['complete'] is False


@pytest.mark.asyncio
async def test_service_scene_coverage_report_override_html_dry_run(session: AsyncSession) -> None:
    """by-id + components_html_override → 用草稿 HTML 覆盖库里的（存前 dry-run 自己的改动）。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = await _seed_identity(session, tag)
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='草稿覆盖',
        content=_content('<div>库里当前无场景标记</div>'),
        required_scenes=['brand_website'],
    )
    ds_id = saved['id']
    # 库里 0/5，但用草稿（全齐）覆盖 → 报告按草稿算 complete
    full = _scene(_comp('nav', 'hero', 'features', 'cta', 'footer'), 'brand_website')
    report = await design_system_service.scene_coverage_report(
        session,
        design_system_id=ds_id,
        viewer_owner_hasn_id=owner,
        components_html_override=full,
    )
    assert report['complete'] is True


@pytest.mark.asyncio
async def test_service_scene_coverage_report_acl(session: AsyncSession) -> None:
    """ACL：非 owner 且未被共享 → ForbiddenError（与 get 同 _assert_can_read 判权）；不存在 id → NotFoundError。"""
    tag = uuid.uuid4().hex[:8]
    owner, agent = await _seed_identity(session, tag)
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='私有',
        content=_content(_scene(_comp('nav'), 'brand_website')),
    )
    ds_id = saved['id']
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.scene_coverage_report(
            session, design_system_id=ds_id, viewer_owner_hasn_id=f'h_intruder_{tag}'
        )
    # 不存在的 id → NotFoundError（_get_alive 兜）
    with pytest.raises(errors.NotFoundError):
        await design_system_service.scene_coverage_report(
            session, design_system_id=999_000_222, viewer_owner_hasn_id=owner
        )
