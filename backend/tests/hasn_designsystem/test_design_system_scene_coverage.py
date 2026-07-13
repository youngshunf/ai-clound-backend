"""DSGAL-3 组件画廊「场景标准」——required_scenes 列 + 软提示覆盖标注（零 mock）。

福仔拍板（2026-07-10）：组件画廊按四交付物场景（品牌网站/演示文稿/产品海报/移动端）组织，
owner 派发时勾选要求覆盖哪些场景（默认仅品牌网站）。云端 save 时交叉 owner 的 required_scenes 与
本版 components.manifest 的 scenes[] → 产「品牌网站 3/5 · 缺 行动号召 CTA/页脚」软提示。

⭐ 软提示不阻断：完成判定（发卡时机）仍**只看五项必填字段**（_content_complete），场景缺件只是标注，
   绝不拦发卡——本测试的 test_soft_hint_is_nonblocking 是这条铁律的守卫。

覆盖：
- _normalize_required_scenes 纯函数：只留已知场景、去重保序、空/非法 → 默认 [brand_website]；
- _scene_coverage_annotation + _scene_coverage_hint 纯函数：部分覆盖出精确 3/5 标注与一行文案；全齐 → None；
- 真实 PG：新建 save 默认 required_scenes=[brand_website]；save 显式透传 required_scenes 落库并回显；
- 真实 PG：owner set_required_scenes 改档成功、非 owner ForbiddenError；
- 真实 PG：品牌网站缺件但五必填齐 → 仍发完成卡（软提示不阻断）。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_designsystem.service.design_system_service import (
    Subject,
    _authoritative_scenes,
    _manifest_with_scenes,
    _normalize_required_scenes,
    _scene_coverage_annotation,
    _scene_coverage_hint,
    design_system_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
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


def _complete_content(scenes: list[dict] | None = None) -> dict:
    """一套完整设计系统内容（五项必填全非空）。scenes 传入时并入 manifest 的 scenes[]（DSGAL 覆盖报告）。"""
    manifest: dict = {'groups': [{'name': 'buttons', 'items': ['btn']}]}
    if scenes is not None:
        manifest['scenes'] = scenes
    return {
        'tokens_css': ':root { --bg: #101010; --accent: #2563EB; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明\n本设计系统面向 SaaS 后台。',
        'components_html': '<button class="btn">Go</button>',
        'components_manifest_json': manifest,
        'token_contract_report_json': {'summary': {'score': 88, 'grade': 'good', 'recommendRebuild': False}},
    }


# ── 纯函数：required_scenes 规整 ───────────────────────────────────────────────
def test_normalize_required_scenes_pure() -> None:
    """只留已知场景、去重保序；空/非法/非列表一律回落默认 [brand_website]（零 fake，不臆造未知场景）。"""
    # 已知场景保序去重
    assert _normalize_required_scenes(['brand_website', 'mobile', 'brand_website']) == ['brand_website', 'mobile']
    # 未知场景丢弃
    assert _normalize_required_scenes(['brand_website', '玄学场景', 'deck']) == ['brand_website', 'deck']
    # 空列表 → 默认
    assert _normalize_required_scenes([]) == ['brand_website']
    # 全未知 → 默认
    assert _normalize_required_scenes(['nope', 123, None]) == ['brand_website']
    # 非列表 → 默认
    assert _normalize_required_scenes(None) == ['brand_website']
    assert _normalize_required_scenes('brand_website') == ['brand_website']


# ── 纯函数：覆盖标注 + 一行软提示 ─────────────────────────────────────────────
def test_scene_coverage_annotation_and_hint_pure() -> None:
    """品牌网站配齐 nav/hero/features、缺 cta/footer → 3/5 标注 + 「品牌网站 3/5 · 缺 行动号召 CTA/页脚」。"""
    manifest_scenes = [{'id': 'brand_website', 'presentComponents': ['nav', 'hero', 'features']}]
    ann = _scene_coverage_annotation(['brand_website'], manifest_scenes)
    assert len(ann) == 1
    a = ann[0]
    assert a['id'] == 'brand_website'
    assert a['label'] == '品牌网站'
    assert a['requiredTotal'] == 5
    assert a['presentCount'] == 3
    assert [m['key'] for m in a['missing']] == ['cta', 'footer']
    assert [m['label'] for m in a['missing']] == ['行动号召 CTA', '页脚']
    assert a['complete'] is False
    assert _scene_coverage_hint(ann) == '品牌网站 3/5 · 缺 行动号召 CTA/页脚'

    # 全齐 → complete、hint 为 None（卡片不带累赘）
    full = [{'id': 'brand_website', 'presentComponents': ['nav', 'hero', 'features', 'cta', 'footer']}]
    ann_full = _scene_coverage_annotation(['brand_website'], full)
    assert ann_full[0]['complete'] is True
    assert _scene_coverage_hint(ann_full) is None

    # manifest 未检测到该场景（分身一件没标）→ 视为全缺
    ann_none = _scene_coverage_annotation(['brand_website'], None)
    assert ann_none[0]['presentCount'] == 0
    assert len(ann_none[0]['missing']) == 5


# 品牌网站全 5 件标准组件都打了标记的组件画廊 HTML（check_scenes 会判 complete）。
_BRAND_WEBSITE_FULL_HTML = (
    '<section data-ds-scene="brand_website">'
    '<nav data-ds-component="nav">导航</nav>'
    '<div data-ds-component="hero">首屏</div>'
    '<div data-ds-component="features">特性</div>'
    '<div data-ds-component="cta">立即体验</div>'
    '<footer data-ds-component="footer">页脚</footer>'
    '</section>'
)


# ── 纯函数：零信任——场景覆盖据实际 HTML 重算，绝不信自带 manifest.scenes ─────────
def test_authoritative_scenes_overrides_manifest_pure() -> None:
    """根因守卫：分身把画廊标记补齐了却没重跑抽取器（manifest.scenes 空/漂移），详情页也须显示配齐。

    ``_authoritative_scenes`` 只看 ``components_html``（与 check_scenes 同源 detect_scenes）；
    ``_manifest_with_scenes`` 恒用它覆盖 manifest 里的 ``scenes``，不信分身自带的那份。
    """
    # 1) HTML 有全部品牌网站标记 → 检出 brand_website 且 complete
    scenes = _authoritative_scenes(_BRAND_WEBSITE_FULL_HTML)
    bw = next(s for s in scenes if s['id'] == 'brand_website')
    assert bw['complete'] is True
    assert set(bw['presentComponents']) == {'nav', 'hero', 'features', 'cta', 'footer'}

    # 2) manifest 自带**空/漂移** scenes → 被 HTML 实测覆盖（其余字段保留、不原地改入参）
    stale = {'groups': [{'name': 'x'}], 'scenes': []}  # 分身漏填 → 之前详情页 0/5 的根因
    merged = _manifest_with_scenes(stale, _BRAND_WEBSITE_FULL_HTML)
    assert merged is not stale and stale['scenes'] == []  # 入参不被原地改
    assert merged['groups'] == [{'name': 'x'}]
    merged_bw = next(s for s in merged['scenes'] if s['id'] == 'brand_website')
    assert merged_bw['complete'] is True

    # 3) manifest 缺失但 HTML 检出场景 → 合成最小 {'scenes': [...]}
    synth = _manifest_with_scenes(None, _BRAND_WEBSITE_FULL_HTML)
    assert isinstance(synth, dict) and any(s['id'] == 'brand_website' for s in synth['scenes'])

    # 4) HTML 空/无标记 → 空覆盖；manifest 为 None 且无场景 → 原样返回 None（不臆造）
    assert _authoritative_scenes('') == []
    assert _authoritative_scenes('<div>无标记</div>') == []
    assert _manifest_with_scenes(None, '') is None


# ── 真实 PG：默认值 ────────────────────────────────────────────────────────────
async def test_new_save_defaults_required_scenes(session) -> None:
    """新建 save 不传 required_scenes → 默认 [brand_website]（_ds_dict 回显）。"""
    tag = uuid.uuid4().hex[:8]
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(f'a_{tag}', f'h_{tag}'),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='默认场景',
        content=_complete_content(),
    )
    assert saved['required_scenes'] == ['brand_website']


# ── 真实 PG：save 透传 required_scenes ─────────────────────────────────────────
async def test_agent_save_accepts_required_scenes(session) -> None:
    """save 显式传 required_scenes → 规整落库并回显；再 save 不传 → 沿用存量（不被抹掉）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_{tag}'
    agent = f'a_{tag}'
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='多场景',
        content=_complete_content(),
        required_scenes=['mobile', 'brand_website', '未知', 'mobile'],
    )
    ds_id = saved['id']
    assert saved['required_scenes'] == ['mobile', 'brand_website']  # 去重保序 + 丢未知

    # 再 save 一版但不传 required_scenes → 存量场景要求不被无意抹掉
    again = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=ds_id,
        slug=f'sc-{tag}',
        name='多场景改名',
        content=_complete_content(),
    )
    assert again['required_scenes'] == ['mobile', 'brand_website']


# ── 真实 PG：owner set_required_scenes + ACL ──────────────────────────────────
async def test_owner_set_required_scenes_and_forbidden(session) -> None:
    """owner 改场景要求成功（不动版本内容）；非 owner → ForbiddenError。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_{tag}'
    agent = f'a_{tag}'
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='待改档',
        content=_complete_content(),
    )
    ds_id = saved['id']

    updated = await design_system_service.set_required_scenes(
        session, owner_hasn_id=owner, design_system_id=ds_id, required_scenes=['brand_website', 'deck', 'poster']
    )
    assert updated['required_scenes'] == ['brand_website', 'deck', 'poster']

    # 空列表 → 规整回默认
    reset = await design_system_service.set_required_scenes(
        session, owner_hasn_id=owner, design_system_id=ds_id, required_scenes=[]
    )
    assert reset['required_scenes'] == ['brand_website']

    # 非 owner 改档 → 拒绝
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.set_required_scenes(
            session, owner_hasn_id=f'h_intruder_{tag}', design_system_id=ds_id, required_scenes=['mobile']
        )


# ── 真实 PG：软提示不阻断发卡（DSGAL-3 铁律守卫）─────────────────────────────
async def test_soft_hint_is_nonblocking(session) -> None:
    """品牌网站缺件（3/5）但五项必填齐 → 仍发完成卡：场景覆盖只是软提示，绝不拦发卡。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_{tag}'
    agent = f'a_{tag}'
    # manifest 只标了 nav/hero/features → 品牌网站缺 cta/footer（3/5）
    partial_scenes = [{'id': 'brand_website', 'presentComponents': ['nav', 'hero', 'features']}]
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='缺件但完整',
        content=_complete_content(scenes=partial_scenes),
        required_scenes=['brand_website'],
    )
    # 五必填齐 → 完成水位落地（软提示不阻断）
    assert saved['completed_notified_at'] is not None
    assert saved['required_scenes'] == ['brand_website']


# ── 真实 PG：get 序列化据 HTML 重算 scenes（修「画廊已配齐但详情页 0/N 缺」）─────────
async def test_get_derives_manifest_scenes_from_html_not_stored_manifest(session) -> None:
    """根因回归：存的 manifest 没有 scenes[]（分身漏填），但画廊 HTML 标记齐全 →

    ``get`` 返回的 ``components_manifest_json.scenes`` 必须据实际 HTML 重算出 brand_website 配齐，
    与 check_scenes 逐场景一致。这样存量行无需重存、详情页覆盖分区即修好。
    """
    tag = uuid.uuid4().hex[:8]
    owner = f'h_{tag}'
    agent = f'a_{tag}'
    # 关键构造：manifest **不含** scenes[]（模拟分身补齐画廊标记却没重跑抽取器），但 HTML 标记齐全
    content = {
        'tokens_css': ':root { --accent: #2563EB; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': _BRAND_WEBSITE_FULL_HTML,
        'components_manifest_json': {'groups': [{'name': 'buttons'}]},  # 无 scenes 键
        'token_contract_report_json': {'summary': {'score': 90, 'grade': 'good', 'recommendRebuild': False}},
    }
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'sc-{tag}',
        name='画廊齐但manifest漏scenes',
        content=content,
        required_scenes=['brand_website'],
    )
    ds_id = saved['id']

    got = await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=owner)
    manifest = got['current_revision']['components_manifest_json']
    assert isinstance(manifest, dict)
    scenes = manifest.get('scenes')
    assert isinstance(scenes, list) and scenes, 'get 必须据 HTML 重算注入 scenes[]，即便存的 manifest 没有'
    bw = next(s for s in scenes if s['id'] == 'brand_website')
    assert bw['complete'] is True  # 详情页「品牌网站 5/5 已配齐」，不再假显 0/5
    # 覆盖标注交叉 required_scenes 也应判齐（与详情页 SceneCoveragePanel 同口径）
    ann = _scene_coverage_annotation(saved['required_scenes'], scenes)
    assert ann[0]['complete'] is True
