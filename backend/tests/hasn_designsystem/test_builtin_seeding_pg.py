"""DS-P6 官方内置设计系统播种真实 PG 验收（零 mock）。

覆盖 P6 验收（doc12 P6）+ 内置库修复（福仔 2026-06-19）+ Open Design 150 套重做（ODLIB）：
- seed JSON 150 套去品牌中文化设计系统（源自 Open Design，中文产品名 + 中文分类），评分均 excellent/100、契约合规；
- seed_builtin_design_systems reconcile：seed slug 落成 owner='system'、is_builtin=True、
  source_kind='seed' 的全局只读设计系统 + 首版 revision（含完整 token 契约内容）+ 预览色板；
- 幂等：内容未变再跑一次零变更（created/updated/retired 全空）；
- 换代：改 content_hash → 落新版 + 切当前版（updated）；
- 退役：seed 里没有的旧 builtin slug → 软删（retired）；
- 每套带 preview_swatches（列表卡预览图，{bg,surface,fg,...}）。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。session 末尾回滚，不留脏数据
（seed 函数不 commit，begin_nested SAVEPOINT 随外层事务回滚）。
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_designsystem.model import DesignSystem, Revision
from backend.app.hasn_designsystem.service.builtin_seeding_service import (
    _SEED_PATH,
    seed_builtin_design_systems,
)
from backend.app.hasn_designsystem.service.design_system_service import BUILTIN_OWNER
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# 预览色板期望的关键键（前端列表卡渲染迷你 mockup 用）。
_PREVIEW_KEYS = {'bg', 'surface', 'fg', 'muted', 'border', 'accent', 'accent_on'}


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


def _load_seed_entries() -> list[dict]:
    return json.loads(Path(_SEED_PATH).read_text(encoding='utf-8'))


def test_seed_json_meets_acceptance_gate() -> None:
    """seed JSON 静态准入：≥10 套、评分均 ≥80、品类多样、产品视角中文命名、内容齐全 + 预览色板（不需 DB）。"""
    entries = _load_seed_entries()
    assert len(entries) >= 10, f'官方内置库不足 10 套（实际 {len(entries)}）'
    slugs = {e['slug'] for e in entries}
    assert len(slugs) == len(entries), 'seed slug 有重复'
    cats = {e['category'] for e in entries}
    assert all(c for c in cats), '存在空品类'
    assert len(cats) >= 6, f'品类多样性不足（仅 {len(cats)} 类）'
    for e in entries:
        # 产品视角中文命名：名称非空、非 slug 原样（去掉「官方暖沙 / seed」这类机械命名）
        assert e['name'] and e['name'] != e['slug'], f'{e["slug"]} 缺产品名'
        assert isinstance(e['score'], int) and e['score'] >= 80, f'{e["slug"]} 评分未达 80'
        assert e['grade'] in {'excellent', 'good'}, f'{e["slug"]} 等级 {e["grade"]} 不合格'
        assert e['source_kind'] == 'seed'
        # 预览色板：列表卡预览图所需，关键键齐全
        sw = e.get('preview_swatches')
        assert isinstance(sw, dict) and set(sw) >= _PREVIEW_KEYS, f'{e["slug"]} 预览色板缺键'
        assert all(isinstance(v, str) and v for v in sw.values()), f'{e["slug"]} 预览色板有空值'
        c = e['content']
        # 四层 token 契约产物齐全（真源 + 派生 + 评分报告 + 组件 + 设计说明）
        assert c['tokens_css'] and ':root' in c['tokens_css'], f'{e["slug"]} tokens.css 缺失'
        assert isinstance(c['design_tokens_json'], dict) and c['design_tokens_json'].get('summary')
        assert c['tailwind_css'] and '@theme' in c['tailwind_css']
        # 设计说明（中文）：150 套各一段简洁说明（配色/排版/圆角/场景），非空且非占位 stub。
        assert c['design_md'] and len(c['design_md']) > 40, f'{e["slug"]} 设计说明过短'
        assert c['components_html'] and '<' in c['components_html']
        assert isinstance(c['components_manifest_json'], dict)
        assert isinstance(c['token_contract_report_json'], dict)


@pytest.mark.asyncio
async def test_seed_inserts_builtin_with_revisions_and_is_idempotent(session: AsyncSession) -> None:
    """seed → 全部 seed slug 落成 builtin 行 + 首版 revision + 预览色板；内容未变再跑零变更（幂等）。"""
    entries = _load_seed_entries()
    seed_slugs = {e['slug'] for e in entries}
    by_slug = {e['slug']: e for e in entries}

    # 首次播种（dev 库若已存在部分 builtin，本次只补缺/换代；最终状态断言不依赖返回值多少）
    await seed_builtin_design_systems(session)

    # 最终状态：所有 seed slug 都以 builtin 行存在（owner='system'）
    rows = (
        (
            await session.execute(
                select(DesignSystem).where(
                    DesignSystem.owner_hasn_id == BUILTIN_OWNER,
                    DesignSystem.is_builtin.is_(True),
                    DesignSystem.slug.in_(seed_slugs),
                )
            )
        )
        .scalars()
        .all()
    )
    got_slugs = {r.slug for r in rows}
    assert got_slugs == seed_slugs, f'缺失 builtin: {seed_slugs - got_slugs}'

    cats_seen = set()
    for r in rows:
        expected = by_slug[r.slug]
        assert r.source_kind == 'seed'
        assert r.is_builtin is True
        assert r.score is not None and r.score >= 80, f'{r.slug} 评分 {r.score} < 80'
        assert r.grade in {'excellent', 'good'}
        assert r.category == expected['category']
        assert r.content_hash, f'{r.slug} content_hash 未回填'
        assert r.current_revision_id is not None, f'{r.slug} 未回填 current_revision_id'
        # 列表卡预览色板已 denorm 落库（前端列表预览图所需）
        assert isinstance(r.preview_swatches, dict) and set(r.preview_swatches) >= _PREVIEW_KEYS, (
            f'{r.slug} 预览色板未落库'
        )
        cats_seen.add(r.category)

        # 首版 revision 内容齐全（离线镜像/预览所需）
        rev = await session.get(Revision, r.current_revision_id)
        assert rev is not None and rev.design_system_id == r.id
        assert rev.author_id == 'system'
        assert rev.tokens_css and ':root' in rev.tokens_css
        assert isinstance(rev.design_tokens_json, dict) and rev.design_tokens_json.get('summary')
        assert rev.design_md and len(rev.design_md) > 40

    assert len(cats_seen) >= 6, f'落库品类多样性不足（仅 {len(cats_seen)} 类）'

    # 幂等：内容未变 → 第二次 reconcile 零变更（不依赖 dev 库其它 builtin → 只断言本批 slug 不在 created/updated）
    second = await seed_builtin_design_systems(session)
    assert set(second['created']) & seed_slugs == set(), f'幂等失败：又新建了 {set(second["created"]) & seed_slugs}'
    assert set(second['updated']) & seed_slugs == set(), f'幂等失败：又换代了 {set(second["updated"]) & seed_slugs}'

    # 没有重复行（每个 slug 在 owner='system' 下唯一）
    dup_check = (
        (
            await session.execute(
                select(DesignSystem.slug).where(
                    DesignSystem.owner_hasn_id == BUILTIN_OWNER,
                    DesignSystem.is_builtin.is_(True),
                    DesignSystem.slug.in_(seed_slugs),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(dup_check) == len(set(dup_check)) == len(seed_slugs), '存在重复 builtin 行'


@pytest.mark.asyncio
async def test_reconcile_retires_stale_builtin_and_replaces_changed(session: AsyncSession) -> None:
    """reconcile：seed 里没有的旧 builtin → 退役软删；content_hash 漂移的 → 换代落新版。"""
    # 先播一遍（确保本批 seed 都在）。
    await seed_builtin_design_systems(session)

    # 造一个「历史遗留」旧官方库（seed 里绝无此 slug），对账应将其退役。
    stale = DesignSystem(
        owner_hasn_id=BUILTIN_OWNER,
        name='官方暖沙(历史遗留)',
        slug='legacy-warm-sand-xyz',
        is_builtin=True,
        source_kind='seed',
        content_hash='stalehash',
    )
    session.add(stale)
    await session.flush()
    stale_id = stale.id

    # 取一个真实 seed slug，手动改它的 content_hash 制造「漂移」，对账应将其换代。
    entries = _load_seed_entries()
    victim_slug = entries[0]['slug']
    victim = (
        await session.execute(
            select(DesignSystem).where(
                DesignSystem.owner_hasn_id == BUILTIN_OWNER,
                DesignSystem.is_builtin.is_(True),
                DesignSystem.slug == victim_slug,
            )
        )
    ).scalar_one()
    old_rev_id = victim.current_revision_id
    victim.content_hash = 'drifted-hash-force-replace'
    await session.flush()

    result = await seed_builtin_design_systems(session)

    # 退役：legacy slug 进 retired，且已软删（deleted_time 非空）。
    assert 'legacy-warm-sand-xyz' in result['retired'], f'旧官方库未退役: {result}'
    retired = await session.get(DesignSystem, stale_id)
    assert retired is not None and retired.deleted_time is not None, '退役应软删而非物删'

    # 换代：victim slug 进 updated，current_revision 已切到新版（rev_no 增），内容回归 seed。
    assert victim_slug in result['updated'], f'漂移项未换代: {result}'
    await session.refresh(victim)
    assert victim.current_revision_id != old_rev_id, '换代应切到新版 revision'
    assert victim.content_hash != 'drifted-hash-force-replace', '换代应把 content_hash 刷回 seed 真值'
    assert isinstance(victim.preview_swatches, dict), '换代应刷新预览色板'
