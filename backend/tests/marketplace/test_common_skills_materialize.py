"""云端侧公共技能共享目录 reconciler 测试（doc11 §5.3.5 / §6 B3，真实 PG + tmp_path）。

直接 await reconcile_shared_common_skills（不经 celery broker）。取 zip 字节经注入的
fetch_zip 提供**构造出来的真实 zip 物料**（含 SKILL.md + 附属文件；注入真实测试物料，
非 mock 业务逻辑——目录物化 / 增量判据 / prune / index 账本全走真实实现）。

覆盖：
  1. 首轮物化：skills/<slug>/SKILL.md 落盘、.index.json 契约结构（revision/skills/updated_at）、
     revision 与 get_common_skill_snapshot 同源一致、锁文件释放。
  2. 二轮增量：指纹未变 → kept、该技能零重下。
  3. 下架 prune（评审 D6）：取消 is_common → 目录 + index 条目删除。
  4. 失败保留旧 revision（零 fake）：单技能下载失败 → 进 failed 清单、revision 不推进，
     成功技能条目仍先记入 index。

seed 不 commit、末尾 rollback，不污染共享本地库。需要 export DATABASE_PORT=15432。
注意：本地库既有的真实公共技能同样会被 reconcile（fetch 已注入，不出网），断言只针对
本测试 seed 的技能，不对全量计数做等值断言。
"""

from __future__ import annotations

import json
import uuid
import zipfile

from collections import Counter
from io import BytesIO

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.service.common_skills_materialize_service import (
    reconcile_shared_common_skills,
)
from backend.app.marketplace.service.common_skills_service import get_common_skill_snapshot
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


def _skill_zip_bytes(skill_id: str) -> bytes:
    """构造最小合法技能 zip（SKILL.md + 附属文件），内容随 skill_id 可辨。"""
    slug = skill_id.rsplit('/', 1)[-1]
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('SKILL.md', f'---\nname: {slug}\ndescription: 共享目录测试\n---\n# {slug}\n正文 {skill_id}\n')
        zf.writestr('references/notes.md', f'{skill_id} 附属物料\n')
    return buf.getvalue()


class _ZipFetcher:
    """注入的取包实现：返回构造的真实 zip 字节，计数每技能取包次数，可指定失败技能。"""

    def __init__(self, fail_ids: set[str] | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self.fail_ids = fail_ids or set()

    async def __call__(self, db, skill_id: str) -> bytes:
        self.calls[skill_id] += 1
        if skill_id in self.fail_ids:
            raise RuntimeError(f'injected download failure for {skill_id}')
        return _skill_zip_bytes(skill_id)


async def _seed_common_skill(session, skill_id: str, fingerprint: str) -> MarketplaceSkill:
    namespace, slug = skill_id.rsplit('/', 1)
    skill = MarketplaceSkill(
        skill_id=skill_id,
        namespace=namespace,
        slug=slug,
        name=slug,
        status='published',
        visibility='public',
        is_common=True,
    )
    session.add(skill)
    session.add(
        MarketplaceSkillVersion(skill_id=skill_id, version='1.0.0', content_hash=fingerprint, is_latest=True)
    )
    await session.flush()
    return skill


async def test_reconcile_materialize_keep_and_prune(session, tmp_path) -> None:
    skill_id = f'huanxing/test/mat-{uuid.uuid4().hex[:8]}'
    slug = skill_id.rsplit('/', 1)[-1]
    skill = await _seed_common_skill(session, skill_id, 'fp-mat-1')
    fetcher = _ZipFetcher()

    # ---- 首轮：物化 + index 契约 ----
    stats = await reconcile_shared_common_skills(session, tmp_path, fetch_zip=fetcher)
    assert skill_id in stats['materialized']
    assert stats['failed'] == []

    common_dir = tmp_path / 'common'
    skill_md = common_dir / 'skills' / slug / 'SKILL.md'
    assert skill_md.exists()
    assert skill_id in skill_md.read_text(encoding='utf-8')
    assert (common_dir / 'skills' / slug / 'references' / 'notes.md').exists()
    assert not (common_dir / '.lock').exists()  # 锁已释放

    index = json.loads((common_dir / '.index.json').read_text(encoding='utf-8'))
    assert set(index.keys()) >= {'revision', 'skills', 'updated_at'}  # 三方 writer 契约结构
    assert index['skills'][skill_id] == {'slug': slug, 'fingerprint': 'fp-mat-1'}
    _, snapshot_rev = await get_common_skill_snapshot(session)
    assert index['revision'] == snapshot_rev == stats['revision']

    # ---- 二轮：指纹未变 → kept、零重下 ----
    first_round_calls = fetcher.calls[skill_id]
    assert first_round_calls == 1
    stats2 = await reconcile_shared_common_skills(session, tmp_path, fetch_zip=fetcher)
    assert skill_id in stats2['kept']
    assert fetcher.calls[skill_id] == first_round_calls  # 未重下

    # ---- 下架 prune（评审 D6）：取消 is_common → 目录 + index 条目删除 ----
    skill.is_common = False
    await session.flush()
    stats3 = await reconcile_shared_common_skills(session, tmp_path, fetch_zip=fetcher)
    assert skill_id in stats3['pruned']
    assert not (common_dir / 'skills' / slug).exists()
    index3 = json.loads((common_dir / '.index.json').read_text(encoding='utf-8'))
    assert skill_id not in index3['skills']
    _, snapshot_rev3 = await get_common_skill_snapshot(session)
    assert index3['revision'] == snapshot_rev3


async def test_reconcile_partial_failure_keeps_old_revision(session, tmp_path) -> None:
    """单技能下载失败：进 failed 清单、revision 不推进（零 fake），成功条目仍先记入 index。"""
    ok_id = f'huanxing/test/mat-ok-{uuid.uuid4().hex[:8]}'
    bad_id = f'huanxing/test/mat-bad-{uuid.uuid4().hex[:8]}'
    await _seed_common_skill(session, ok_id, 'fp-ok-1')
    await _seed_common_skill(session, bad_id, 'fp-bad-1')
    fetcher = _ZipFetcher(fail_ids={bad_id})

    stats = await reconcile_shared_common_skills(session, tmp_path, fetch_zip=fetcher)
    assert {f['skill_id'] for f in stats['failed']} == {bad_id}
    assert ok_id in stats['materialized']

    common_dir = tmp_path / 'common'
    index = json.loads((common_dir / '.index.json').read_text(encoding='utf-8'))
    _, cloud_rev = await get_common_skill_snapshot(session)
    assert not index['revision']  # 部分失败 → 保留旧（空账本）revision，下轮重试
    assert index['revision'] != cloud_rev
    assert not stats['revision']
    assert ok_id in index['skills']  # 成功条目先记入，下轮增量不重下
    assert bad_id not in index['skills']
    assert not (common_dir / 'skills' / bad_id.rsplit('/', 1)[-1]).exists()  # 失败技能无假物料
