"""WSPUSH-M2：配置/目录变更 WS 主动推送——写点 revision 一致性真实 PG 验收（零 mock）。

设计事实源：docs/hasn-node设计文档/02-数据与同步/07-配置与目录变更的WS主动推送与sync_agents轮询退役设计.md

核心不变量（写点 bump 推/缓存的 revision，必须 == daemon 拉取时各自数据源计算出的权威 revision）：
- platform_config：bump('platform_config') == update_config 返回的 revision（覆盖式写后一致）。
- common_skills：bump('common_skills') == get_common_skill_snapshot()[1]（委派一致）。
- builtin_catalog：compute_builtin_catalog_revision() 跑真实 schema 返回稳定指纹；
  插入一行 catalog（事务末尾回滚）→ 指纹变化。

事务末尾回滚，不留脏数据（update_config/插入仅 flush，不 commit）。需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import sync_invalidate_service as svc
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.hasn_designsystem.model.design_system import DesignSystem
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.app.hasn_task.model.task import HasnTask
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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


async def test_platform_config_bump_matches_update_config(session: AsyncSession) -> None:
    """bump('platform_config') 推/缓存的 revision == update_config 写后返回的权威 revision。"""
    current, _rev = await platform_default_config_service.get_effective_config(session)
    resp = await platform_default_config_service.update_config(session, config=current, updated_by='wspush-test')
    bumped = await svc.bump('platform_config', session)
    assert bumped == resp.revision, '推送的 platform_config revision 与写后权威 revision 不一致'


async def test_common_skills_bump_matches_snapshot(session: AsyncSession) -> None:
    """bump('common_skills') 委派 get_common_skill_snapshot，revision 必须一致。"""
    from backend.app.marketplace.service.common_skills_service import get_common_skill_snapshot

    _, snapshot_rev = await get_common_skill_snapshot(session)
    bumped = await svc.bump('common_skills', session)
    assert bumped == snapshot_rev


async def test_builtin_catalog_revision_real_schema_and_changes(session: AsyncSession) -> None:
    """compute_builtin_catalog_revision 跑真实 schema 返回稳定指纹；新增 catalog 行后指纹变化。"""
    before = await svc.compute_builtin_catalog_revision(session)
    assert isinstance(before, str) and before

    # 插入一行临时 catalog（事务末尾回滚，不污染目录）→ 指纹必须变化
    key = f'wspush_probe_{uuid.uuid4().hex[:8]}'
    session.add(
        HasnBuiltinTaskCatalog(
            builtin_key=key,
            name='WSPUSH 探针任务',
            schedule_type='cron',
            schedule_config={'cron': '0 9 * * *'},
            enabled=True,
            default_enabled=False,
            revision=1,
        )
    )
    await session.flush()
    after = await svc.compute_builtin_catalog_revision(session)
    assert after != before, '新增 catalog 行后 builtin_catalog_revision 未变化'


async def test_designsystem_revision_real_schema_changes_and_bump_consistent(session: AsyncSession) -> None:
    """DS-P5：designsystem_revision 跑真实 schema 返回稳定指纹；新增设计系统行 → 指纹变；

    bump('designsystem') 推/缓存的 revision == compute_designsystem_revision 权威值（一致性）；
    且已纳入 KINDS → get_all_revisions 握手快照含 designsystem 键。事务末尾回滚不留脏数据。
    """
    before = await svc.compute_designsystem_revision(session)
    assert isinstance(before, str) and before

    # 插入一行临时设计系统（事务末尾回滚）→ 全局指纹必须变化
    probe_hash = f'h_{uuid.uuid4().hex[:12]}'
    session.add(
        DesignSystem(
            owner_hasn_id='hasn:human:wspush-ds-probe',
            name='WSPUSH 探针设计系统',
            slug=f'wspush-ds-{uuid.uuid4().hex[:8]}',
            source_kind='generated',
            content_hash=probe_hash,
        )
    )
    await session.flush()
    after = await svc.compute_designsystem_revision(session)
    assert after != before, '新增设计系统行后 designsystem_revision 未变化'

    # bump 推/缓存的 revision == 权威重算值（daemon 拉取时算出同一值才能正确对账）
    bumped = await svc.bump('designsystem', session)
    assert bumped == after, '推送的 designsystem revision 与权威重算值不一致'

    # 已纳入握手全量快照
    revisions = await svc.get_all_revisions(session)
    assert 'designsystem' in revisions, 'get_all_revisions 握手快照缺 designsystem 键'


async def test_owner_tasks_revision_real_schema_changes(session: AsyncSession) -> None:
    """LF-P3：compute_owner_tasks_revision 跑真实 schema——空 owner 返回稳定 EMPTY；

    给某 owner 插入一行任务 → 该 owner 指纹变；该任务 task_revision 自增 → 指纹再变。
    事务末尾回滚，不留脏数据。
    """
    owner = f'hasn:human:lf-tasks-{uuid.uuid4().hex[:8]}'
    # 全新 owner 无任务 → 稳定 EMPTY 指纹
    empty = await svc.compute_owner_tasks_revision(session, owner)
    assert empty == svc.EMPTY_TASKS_REVISION

    task_uuid = f'tsk_{uuid.uuid4().hex}'
    session.add(
        HasnTask(
            owner_id=owner,
            agent_id='hasn:agent:lf-probe',
            name='LF 探针任务',
            prompt='probe',
            schedule_type='once',
            schedule_config={'run_at': '2026-06-19T09:00:00Z'},
            state='scheduled',
            task_uuid=task_uuid,
            task_revision=1,
        )
    )
    await session.flush()
    after_insert = await svc.compute_owner_tasks_revision(session, owner)
    assert after_insert != empty, '插入任务后该 owner 指纹未变化'

    # task_revision 自增（模拟任务定义被改）→ 指纹必须再变
    from sqlalchemy import update

    await session.execute(update(HasnTask).where(HasnTask.task_uuid == task_uuid).values(task_revision=2))
    await session.flush()
    after_bump = await svc.compute_owner_tasks_revision(session, owner)
    assert after_bump != after_insert, 'task_revision 自增后该 owner 指纹未变化'


async def test_bump_owner_tasks_consistent_and_out_of_global_handshake(session: AsyncSession) -> None:
    """LF-P3：bump_owner('tasks', owner) 推送的 revision == compute_owner_tasks_revision 权威值；

    且 tasks 是 owner 定向 kind，**不进** KINDS / get_all_revisions 全局握手快照
    （per-owner 指纹对全局握手无意义）。无在线节点时 push 返回 0，best-effort 不抛。
    """
    owner = f'hasn:human:lf-bump-{uuid.uuid4().hex[:8]}'
    bumped = await svc.bump_owner(svc.KIND_TASKS, session, owner)
    authoritative = await svc.compute_owner_tasks_revision(session, owner)
    assert bumped == authoritative, 'bump_owner 推送的 tasks revision 与权威重算值不一致'

    # tasks 绝不进全局握手快照
    assert svc.KIND_TASKS not in svc.KINDS
    revisions = await svc.get_all_revisions(session)
    assert svc.KIND_TASKS not in revisions, 'owner 定向 tasks 不应出现在全局握手快照'


async def test_bump_owner_rejects_unknown_kind(session: AsyncSession) -> None:
    """bump_owner 只接受 OWNER_KINDS；全局 kind 或未知 kind 必须报错（防误用）。"""
    with pytest.raises(ValueError):
        await svc.bump_owner('builtin_catalog', session, 'hasn:human:x')
    with pytest.raises(ValueError):
        await svc.bump_owner('nonexistent', session, 'hasn:human:x')


async def test_owner_community_revision_real_schema_changes(session: AsyncSession) -> None:
    """LFRT 刀4：compute_owner_community_revision 跑真实 schema——空 owner 返回稳定 EMPTY；

    给某 owner 插入帖子 → 该 owner 指纹变；帖子 updated_time 变化 → 指纹再变；
    再插入文章 → 指纹又变（帖子+文章都进指纹）。事务末尾回滚，不留脏数据。
    """
    from backend.app.hasn_community.model import HasnArticles, HasnPosts
    from backend.utils.timezone import timezone

    owner = f'h_lfrt_cm_{uuid.uuid4().hex[:8]}'
    empty = await svc.compute_owner_community_revision(session, owner)
    assert empty == svc.EMPTY_COMMUNITY_REVISION

    post_id = f'p_{uuid.uuid4().hex[:12]}'
    session.add(
        HasnPosts(
            post_id=post_id,
            author_type='agent',
            author_hasn_id='a_lfrt_probe',
            owner_hasn_id=owner,
            content='LFRT 探针帖子',
            visibility='public',
            comment_policy='all',
            generation_type='agent',
            status='published',
        )
    )
    await session.flush()
    after_post = await svc.compute_owner_community_revision(session, owner)
    assert after_post != empty, '插入帖子后该 owner 社区指纹未变化'

    # updated_time 变化（模拟帖子被编辑）→ 指纹必须再变
    from sqlalchemy import update

    await session.execute(update(HasnPosts).where(HasnPosts.post_id == post_id).values(updated_time=timezone.now()))
    await session.flush()
    after_touch = await svc.compute_owner_community_revision(session, owner)
    assert after_touch != after_post, '帖子 updated_time 变化后该 owner 社区指纹未变化'

    # 文章也进指纹（帖子+文章合并口径）
    session.add(
        HasnArticles(
            article_id=f'ar_{uuid.uuid4().hex[:12]}',
            author_type='agent',
            author_hasn_id='a_lfrt_probe',
            owner_hasn_id=owner,
            title='LFRT 探针文章',
            content='正文',
            visibility='public',
            comment_policy='all',
            generation_type='agent',
            status='published',
        )
    )
    await session.flush()
    after_article = await svc.compute_owner_community_revision(session, owner)
    assert after_article != after_touch, '插入文章后该 owner 社区指纹未变化'


async def test_owner_decks_revision_real_schema_changes_and_soft_delete_excluded(session: AsyncSession) -> None:
    """LFRT 刀4：compute_owner_decks_revision 跑真实 schema——空 owner 返回稳定 EMPTY；

    插入 deck → 指纹变；rev 自增 → 指纹再变；软删（deleted_time 置位）→ 排除出指纹（回到 EMPTY）。
    事务末尾回滚，不留脏数据。
    """
    from backend.app.hasn_deck.model.deck import Deck
    from backend.utils.timezone import timezone

    owner = f'h_lfrt_dk_{uuid.uuid4().hex[:8]}'
    empty = await svc.compute_owner_decks_revision(session, owner)
    assert empty == svc.EMPTY_DECKS_REVISION

    deck = Deck(owner_id=owner, title='LFRT 探针 deck', status='draft', language='zh', source='agent', rev=1)
    session.add(deck)
    await session.flush()
    after_insert = await svc.compute_owner_decks_revision(session, owner)
    assert after_insert != empty, '插入 deck 后该 owner 指纹未变化'

    # rev 单调自增（deck 被分身改写）→ 指纹必须再变
    from sqlalchemy import update

    await session.execute(update(Deck).where(Deck.id == deck.id).values(rev=2))
    await session.flush()
    after_rev = await svc.compute_owner_decks_revision(session, owner)
    assert after_rev != after_insert, 'deck rev 自增后该 owner 指纹未变化'

    # 软删排除：deleted_time 非空的 deck 不进指纹
    await session.execute(update(Deck).where(Deck.id == deck.id).values(deleted_time=timezone.now()))
    await session.flush()
    after_delete = await svc.compute_owner_decks_revision(session, owner)
    assert after_delete == svc.EMPTY_DECKS_REVISION, '软删 deck 仍被计入 owner 指纹'


async def test_bump_owner_community_and_decks_consistent_and_out_of_global_handshake(session: AsyncSession) -> None:
    """LFRT 刀4：bump_owner('community'/'decks') 推送的 revision == 各自权威重算值；

    两者都是 owner 定向 kind，**不进** KINDS / get_all_revisions 全局握手快照。
    无在线节点时 push 返回 0，best-effort 不抛。
    """
    owner = f'h_lfrt_bp_{uuid.uuid4().hex[:8]}'

    bumped_cm = await svc.bump_owner(svc.KIND_COMMUNITY, session, owner)
    authoritative_cm = await svc.compute_owner_community_revision(session, owner)
    assert bumped_cm == authoritative_cm, 'bump_owner 推送的 community revision 与权威重算值不一致'

    bumped_dk = await svc.bump_owner(svc.KIND_DECKS, session, owner)
    authoritative_dk = await svc.compute_owner_decks_revision(session, owner)
    assert bumped_dk == authoritative_dk, 'bump_owner 推送的 decks revision 与权威重算值不一致'

    assert svc.KIND_COMMUNITY not in svc.KINDS
    assert svc.KIND_DECKS not in svc.KINDS
    revisions = await svc.get_all_revisions(session)
    assert svc.KIND_COMMUNITY not in revisions, 'owner 定向 community 不应出现在全局握手快照'
    assert svc.KIND_DECKS not in revisions, 'owner 定向 decks 不应出现在全局握手快照'
