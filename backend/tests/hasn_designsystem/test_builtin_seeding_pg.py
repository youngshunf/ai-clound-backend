"""DS-P6 官方内置设计系统播种真实 PG 验收（零 mock）。

覆盖 P6 验收（doc12 P6）：
- seed JSON 精选 ≥10 套（实际 15 套）品类多样的高分设计系统，评分均 ≥ good/80、契约合规；
- seed_builtin_design_systems 把它们 INSERT-only 落成 owner='system'、is_builtin=True、
  source_kind='seed' 的全局只读设计系统 + 首版 revision（含完整 token 契约内容）；
- 幂等：再跑一次不重复（返回空）；
- 分类映射到唤星归一品类（ai/saas/developer/minimal/fintech/ecommerce/media/creative/social）。

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

# 唤星归一品类白名单（生成器 CURATED 的映射目标）。
_HUANXING_CATEGORIES = {
    'ai',
    'saas',
    'developer',
    'minimal',
    'fintech',
    'ecommerce',
    'media',
    'creative',
    'social',
}


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
    """seed JSON 静态准入：≥10 套、评分均 ≥80、品类多样、内容齐全（不需要 DB）。"""
    entries = _load_seed_entries()
    assert len(entries) >= 10, f'官方内置库不足 10 套（实际 {len(entries)}）'
    slugs = {e['slug'] for e in entries}
    assert len(slugs) == len(entries), 'seed slug 有重复'
    cats = {e['category'] for e in entries}
    assert cats <= _HUANXING_CATEGORIES, f'存在未归一品类: {cats - _HUANXING_CATEGORIES}'
    assert len(cats) >= 6, f'品类多样性不足（仅 {len(cats)} 类）'
    for e in entries:
        assert isinstance(e['score'], int) and e['score'] >= 80, f'{e["slug"]} 评分未达 80'
        assert e['grade'] in {'excellent', 'good'}, f'{e["slug"]} 等级 {e["grade"]} 不合格'
        assert e['source_kind'] == 'seed'
        c = e['content']
        # 四层 token 契约产物齐全（真源 + 派生 + 评分报告 + 组件 + 设计说明）
        assert c['tokens_css'] and ':root' in c['tokens_css'], f'{e["slug"]} tokens.css 缺失'
        assert isinstance(c['design_tokens_json'], dict) and c['design_tokens_json'].get('summary')
        assert c['tailwind_css'] and '@theme' in c['tailwind_css']
        assert c['design_md'] and len(c['design_md']) > 100, f'{e["slug"]} DESIGN.md 过短'
        assert c['components_html'] and '<' in c['components_html']
        assert isinstance(c['components_manifest_json'], dict)
        assert isinstance(c['token_contract_report_json'], dict)


@pytest.mark.asyncio
async def test_seed_inserts_builtin_with_revisions_and_is_idempotent(session: AsyncSession) -> None:
    """seed → 全部 seed slug 落成 builtin 行 + 首版 revision；再跑一次返回空（INSERT-only 幂等）。"""
    entries = _load_seed_entries()
    seed_slugs = {e['slug'] for e in entries}
    by_slug = {e['slug']: e for e in entries}

    # 首次播种（dev 库若已存在部分 builtin，本次只补缺；最终状态断言不依赖返回值多少）
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
        assert r.category in _HUANXING_CATEGORIES
        assert r.category == expected['category']
        assert r.content_hash, f'{r.slug} content_hash 未回填'
        assert r.current_revision_id is not None, f'{r.slug} 未回填 current_revision_id'
        cats_seen.add(r.category)

        # 首版 revision 内容齐全（离线镜像/预览所需）
        rev = await session.get(Revision, r.current_revision_id)
        assert rev is not None and rev.design_system_id == r.id
        assert rev.rev_no == 1
        assert rev.author_id == 'system'
        assert rev.tokens_css and ':root' in rev.tokens_css
        assert isinstance(rev.design_tokens_json, dict) and rev.design_tokens_json.get('summary')
        assert rev.design_md and len(rev.design_md) > 100

    assert len(cats_seen) >= 6, f'落库品类多样性不足（仅 {len(cats_seen)} 类）'

    # 幂等：所有 slug 已存在 → 第二次播种零新增
    second = await seed_builtin_design_systems(session)
    assert second == [], f'幂等失败：第二次播种又插了 {second}'

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
