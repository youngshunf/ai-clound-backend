"""doc19 S2 项目记忆真实 PG 验收（零 mock）。

设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md
  §6 项目记忆（§6.1 模型 / §6.2 上下文自动带入 / §6.3 可见性）· §4.3 supersedes_hint
  · §8.2 增列汇总 · 决策 D-5 / D-6 / D-21

钉死的核心不变量：

1. **写继承**（§6.2）：有项目语境且调用方没表达 scope 意图 → 自动落
   `scope_kind='project'` + `scope_id=<云端权威项目 UUID>`；调用方**显式**给了 scope → 永远
   照办，项目语境不许覆盖它；
2. **读不收窄**（铁律「项目轴写继承·读不收窄」+ §6.2 并集检索）：在项目里检索，**必定**
   看得见全局常识，只是看不见**别的项目**的专属事实。若这条测试变红，说明分身一进项目
   就与自己的全局记忆断联——比「没打通项目记忆」更糟；
3. **world + project 合法**（§6.1 / D-5）：项目记忆就是这个组合，不许被 `world` 主体的
   「不许 global 作用域」纠偏逻辑顺手改掉；
4. `supersedes_hint` 落**正式列**（§8.2），不再塞 `source_refs_json`。

需本地 PostgreSQL :15432（不可达则跳过，不伪造）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.model.semantic_fact import SemanticFact
from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine

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
        # 全局 async_db_session 池绑上一事件循环；每测试后释放，避免下个测试 different loop。
        await async_engine.dispose()


async def _cleanup(session: AsyncSession, owner_id: str) -> None:
    await session.execute(delete(SemanticFact).where(SemanticFact.owner_id == owner_id))
    await session.commit()


def _owner() -> str:
    return f'h_p19_{uuid.uuid4().hex[:8]}'


def _agent() -> str:
    return f'a_p19_{uuid.uuid4().hex[:8]}'


def _project() -> str:
    """云端权威项目 UUID（铁律：scope_id 只存云端 UUID，禁止本地 ID）。"""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------------------
# 一、写继承：项目语境自动带入（§6.2）
# --------------------------------------------------------------------------------------


async def test_save_inherits_project_scope_when_no_explicit_scope(session: AsyncSession) -> None:
    """有项目语境 + 未显式给 scope → 自动落 project 作用域 + 云端项目 UUID。"""
    owner, agent, project = _owner(), _agent(), _project()
    try:
        out = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value='提交信息用中文',
            project_id=project,
        )
        await session.commit()
        assert out['scope_kind'] == 'project'
        assert out['scope_id'] == project

        row = (await session.execute(select(SemanticFact).where(SemanticFact.fact_id == out['fact_id']))).scalar_one()
        assert row.scope_kind == 'project'
        assert row.scope_id == project
    finally:
        await _cleanup(session, owner)


async def test_save_without_project_context_keeps_legacy_fallback(session: AsyncSession) -> None:
    """无项目语境 → 兜底逻辑一字不动：scope_kind='global'、scope_id 回落主体 id。"""
    owner, agent = _owner(), _agent()
    try:
        out = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='偏好',
            object_value='冰美式',
        )
        await session.commit()
        assert out['scope_kind'] == 'global'
        assert out['scope_id'] == agent
    finally:
        await _cleanup(session, owner)


@pytest.mark.parametrize(
    ('explicit_kind', 'explicit_id'),
    [
        ('global', None),  # 显式要全局：项目语境不许把它拽进项目
        ('topic', 'topic:rust'),  # 显式要话题作用域
        (None, 'scope_only_no_kind'),  # 只给 scope_id 也算表达了意图
    ],
)
async def test_explicit_scope_beats_project_context(
    session: AsyncSession, explicit_kind: str | None, explicit_id: str | None
) -> None:
    """显式传 scope_* → 原样采用，绝不被项目语境覆盖（§6.2「显式传参优先」）。"""
    owner, agent, project = _owner(), _agent(), _project()
    try:
        out = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='偏好',
            object_value='显式作用域',
            scope_kind=explicit_kind,
            scope_id=explicit_id,
            project_id=project,
        )
        await session.commit()
        assert out['scope_id'] != project, '显式给了 scope 就不该被项目语境接管'
        if explicit_kind:
            assert out['scope_kind'] == explicit_kind
        else:
            # 只给 scope_id：kind 走既有兜底 global，id 照办
            assert out['scope_kind'] == 'global'
            assert out['scope_id'] == explicit_id
    finally:
        await _cleanup(session, owner)


async def test_world_subject_plus_project_scope_survives_world_correction(session: AsyncSession) -> None:
    """§6.1/D-5：项目记忆 = world 主体 + project 作用域，不许被 world 纠偏逻辑改掉。

    纠偏只针对 `world + global`（表 CHECK ck_semantic_fact_world_scope 不许这个组合），
    project 作用域完全合法，落库后必须原样保留。
    """
    owner, agent, project = _owner(), _agent(), _project()
    try:
        # ① 自动带入路径
        auto = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='world',
            subject_id=f'proj_{project[:20]}',  # subject_id 是 varchar(40)，别用完整 'proj:<uuid>'（41 字符）
            predicate='技术决策',
            object_value='本项目数据库用 PostgreSQL 16',
            project_id=project,
        )
        # ② 显式指定路径
        explicit = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='world',
            subject_id=f'proj_{project[:20]}',  # subject_id 是 varchar(40)，别用完整 'proj:<uuid>'（41 字符）
            predicate='踩过的坑',
            object_value='codegen 会改坏 router',
            scope_kind='project',
            scope_id=project,
        )
        await session.commit()

        for out in (auto, explicit):
            assert out['subject_kind'] == 'world'
            assert out['scope_kind'] == 'project', 'world + project 是项目记忆的正规形态，不该被纠偏'
            assert out['scope_id'] == project
            assert out['agent_id'] is None  # world 主体 agent_id 必空（表 CHECK）

        # world + global 仍然被纠偏（既有行为不许回归）
        corrected = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='world',
            subject_id='geo:beijing',
            predicate='常识',
            object_value='北京是中国首都',
            scope_kind='global',
        )
        await session.commit()
        assert corrected['scope_kind'] == 'topic'
    finally:
        await _cleanup(session, owner)


# --------------------------------------------------------------------------------------
# 二、并集检索：读不收窄（§6.2 + 铁律「项目轴写继承·读不收窄」）
# --------------------------------------------------------------------------------------


async def _seed_three_scopes(session: AsyncSession, owner: str, agent: str, marker: str) -> dict[str, str]:
    """造 3 条同关键词事实：当前项目 / 另一个项目 / 全局。返回 {档位: fact_id}。"""
    here = _project()
    elsewhere = _project()
    mine = await semantic_fact_service.save_fact(
        session,
        owner_id=owner,
        agent_id=agent,
        subject_kind='agent_self',
        predicate='部署方式',
        object_value=f'{marker}·本项目走 systemd',
        project_id=here,
    )
    theirs = await semantic_fact_service.save_fact(
        session,
        owner_id=owner,
        agent_id=agent,
        subject_kind='agent_self',
        predicate='部署方式',
        object_value=f'{marker}·别的项目走 docker',
        project_id=elsewhere,
    )
    globally = await semantic_fact_service.save_fact(
        session,
        owner_id=owner,
        agent_id=agent,
        subject_kind='agent_self',
        predicate='部署方式',
        object_value=f'{marker}·一律先跑质量门',
        scope_kind='global',
    )
    await session.commit()
    return {
        'here': here,
        'elsewhere': elsewhere,
        'mine': mine['fact_id'],
        'theirs': theirs['fact_id'],
        'global': globally['fact_id'],
    }


async def test_union_search_returns_project_plus_global_and_excludes_other_project(
    session: AsyncSession,
) -> None:
    """并集检索：当前项目 + 全局都在，**另一个项目的不在**——三条读路口径一致。"""
    owner, agent = _owner(), _agent()
    marker = f'并集{uuid.uuid4().hex[:6]}'
    try:
        seeded = await _seed_three_scopes(session, owner, agent, marker)
        project = seeded['here']

        searched = {
            f['fact_id']
            for f in await semantic_fact_service.search_facts(
                session, owner_id=owner, query=marker, project_id=project
            )
        }
        recalled = {
            f['fact_id']
            for f in await semantic_fact_service.recall_facts(
                session, owner_id=owner, query=marker, project_id=project
            )
        }
        listed_page = await semantic_fact_service.list_facts(
            session, owner_id=owner, agent_id=agent, project_id=project
        )
        listed = {f['fact_id'] for f in listed_page['items']}

        for name, got in (('search', searched), ('recall', recalled), ('list', listed)):
            assert seeded['mine'] in got, f'{name}: 当前项目的专属事实必须在'
            # 读不收窄的钉子：一进项目就丢全局常识，比没打通项目记忆更糟
            assert seeded['global'] in got, f'{name}: 全局事实**必定**在，绝不因为有项目语境而被滤掉'
            assert seeded['theirs'] not in got, f'{name}: 别的项目的专属事实不该串进来'

        # list 的 total 与 items 同判据（否则页码全乱）
        assert listed_page['total'] == len(listed)
    finally:
        await _cleanup(session, owner)


async def test_union_search_keeps_non_project_scopes_visible(session: AsyncSession) -> None:
    """并集判据是「排他项目」而非「只留 project ∪ global」。

    `world` 主体受表 CHECK 约束**不许**用 global 作用域，全局性世界知识只能落 topic/workspace。
    若按字面只留 project ∪ global，分身一进项目就再也看不到任何 world 事实——恰恰把 §6.2 要
    保住的「全局常识」全滤没了。这条测试守的就是那个缺口。
    """
    owner, agent, project = _owner(), _agent(), _project()
    marker = f'世界{uuid.uuid4().hex[:6]}'
    try:
        world_fact = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='world',
            subject_id='tech:rust',
            predicate='常识',
            object_value=f'{marker}·Axum 0.8 路由参数用花括号',
            scope_kind='topic',
            scope_id='tech:rust',
        )
        await session.commit()

        hits = await semantic_fact_service.search_facts(session, owner_id=owner, query=marker, project_id=project)
        assert any(h['fact_id'] == world_fact['fact_id'] for h in hits), (
            'topic 作用域的 world 事实在项目语境下必须仍然可见（读不收窄）'
        )
    finally:
        await _cleanup(session, owner)


async def test_explicit_scope_filter_overrides_project_union(session: AsyncSession) -> None:
    """调用方显式收窄 scope → 按显式条件过滤，项目并集不再插手。"""
    owner, agent = _owner(), _agent()
    marker = f'显式{uuid.uuid4().hex[:6]}'
    try:
        seeded = await _seed_three_scopes(session, owner, agent, marker)

        # 显式只要全局：当前项目的事实也不该出现
        only_global = {
            f['fact_id']
            for f in await semantic_fact_service.search_facts(
                session, owner_id=owner, query=marker, scope_kind='global', project_id=seeded['here']
            )
        }
        assert only_global == {seeded['global']}

        # 显式点名另一个项目（跨项目复盘是正当读法）：只回那一条
        only_theirs = {
            f['fact_id']
            for f in await semantic_fact_service.search_facts(
                session,
                owner_id=owner,
                query=marker,
                scope_kind='project',
                scope_id=seeded['elsewhere'],
                project_id=seeded['here'],
            )
        }
        assert only_theirs == {seeded['theirs']}
    finally:
        await _cleanup(session, owner)


async def test_project_facts_rank_before_global(session: AsyncSession) -> None:
    """§6.2 排序：项目专属事实排在全局之前（在项目里干活，项目约定该先被看见）。"""
    owner, agent = _owner(), _agent()
    marker = f'排序{uuid.uuid4().hex[:6]}'
    try:
        seeded = await _seed_three_scopes(session, owner, agent, marker)
        ordered = [
            f['fact_id']
            for f in await semantic_fact_service.search_facts(
                session, owner_id=owner, query=marker, project_id=seeded['here']
            )
        ]
        assert ordered[0] == seeded['mine'], f'项目专属事实应排首位，实际顺序 {ordered}'
        assert seeded['global'] in ordered
    finally:
        await _cleanup(session, owner)


async def test_no_project_context_reads_everything(session: AsyncSession) -> None:
    """无项目语境 → 不加任何 scope 条件，行为与本切片之前完全一致（含别的项目的事实）。"""
    owner, agent = _owner(), _agent()
    marker = f'无语境{uuid.uuid4().hex[:6]}'
    try:
        seeded = await _seed_three_scopes(session, owner, agent, marker)
        got = {f['fact_id'] for f in await semantic_fact_service.search_facts(session, owner_id=owner, query=marker)}
        assert got == {seeded['mine'], seeded['theirs'], seeded['global']}
    finally:
        await _cleanup(session, owner)


async def test_project_union_still_respects_effective_visibility(session: AsyncSession) -> None:
    """并集检索**不许**弄丢生效可见性过滤（§3.4）：撤回的项目事实照样不可见。"""
    owner, agent, project = _owner(), _agent(), _project()
    marker = f'撤回{uuid.uuid4().hex[:6]}'
    try:
        out = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value=marker,
            project_id=project,
        )
        await session.commit()
        assert any(
            f['fact_id'] == out['fact_id']
            for f in await semantic_fact_service.search_facts(
                session, owner_id=owner, query=marker, project_id=project
            )
        )

        from sqlalchemy import update

        await session.execute(
            update(SemanticFact).where(SemanticFact.fact_id == out['fact_id']).values(status='withdrawn')
        )
        await session.commit()
        session.expire_all()

        assert not await semantic_fact_service.search_facts(
            session, owner_id=owner, query=marker, project_id=project
        )
        assert not await semantic_fact_service.recall_facts(
            session, owner_id=owner, query=marker, project_id=project
        )
        page = await semantic_fact_service.list_facts(session, owner_id=owner, agent_id=agent, project_id=project)
        assert not [f for f in page['items'] if f['fact_id'] == out['fact_id']]
    finally:
        await _cleanup(session, owner)


async def test_project_memory_is_owner_scoped(session: AsyncSession) -> None:
    """§6.3/D-6：项目记忆仍是严格 owner-scoped——**多人同项目共享本期明确不做**。

    同一个项目 UUID 下，另一个主人读不到本主人的项目事实。
    """
    owner, other_owner, agent, project = _owner(), _owner(), _agent(), _project()
    marker = f'私有{uuid.uuid4().hex[:6]}'
    try:
        await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value=marker,
            project_id=project,
        )
        await session.commit()
        # 同一 project_id、不同 owner → 空
        assert (
            await semantic_fact_service.search_facts(
                session, owner_id=other_owner, query=marker, project_id=project
            )
            == []
        )
    finally:
        await _cleanup(session, owner)
        await _cleanup(session, other_owner)


# --------------------------------------------------------------------------------------
# 三、supersedes_hint 正式列（§4.3 / §8.2 / D-21）
# --------------------------------------------------------------------------------------


async def test_supersedes_hint_persists_to_column_and_reads_back(session: AsyncSession) -> None:
    """hint 落正式列、随 search/recall/list 读回；且**不做任何隐式取代**（裁决在 S6 合并）。"""
    owner, agent = _owner(), _agent()
    marker = f'纠正{uuid.uuid4().hex[:6]}'
    try:
        old = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='住址',
            object_value=f'{marker}·老地址',
        )
        new = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='住址',
            object_value=f'{marker}·新地址',
            supersedes_hint=old['fact_id'],
        )
        await session.commit()
        assert new['supersedes_hint'] == old['fact_id']
        assert old['supersedes_hint'] is None

        row = (await session.execute(select(SemanticFact).where(SemanticFact.fact_id == new['fact_id']))).scalar_one()
        assert row.supersedes_hint == old['fact_id']
        # 收敛：不再塞 source_refs_json
        assert 'supersedes_hint' not in row.source_refs_json

        # 三条读路都带回 hint
        by_id = {
            f['fact_id']: f for f in await semantic_fact_service.search_facts(session, owner_id=owner, query=marker)
        }
        assert by_id[new['fact_id']]['supersedes_hint'] == old['fact_id']
        assert by_id[old['fact_id']]['supersedes_hint'] is None

        # 旧事实仍然 active：hint 只是线索，不是已生效的取代关系
        old_row = (
            await session.execute(select(SemanticFact).where(SemanticFact.fact_id == old['fact_id']))
        ).scalar_one()
        assert old_row.status == 'active'
        assert old_row.superseded_by is None
    finally:
        await _cleanup(session, owner)


async def test_supersedes_hint_absent_stays_null(session: AsyncSession) -> None:
    """不带 hint（含空串）→ 列留 NULL，不落空字符串污染合并规则层判据。"""
    owner, agent = _owner(), _agent()
    try:
        out = await semantic_fact_service.save_fact(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='偏好',
            object_value='x',
            supersedes_hint='',
        )
        await session.commit()
        assert out['supersedes_hint'] is None
        row = (await session.execute(select(SemanticFact).where(SemanticFact.fact_id == out['fact_id']))).scalar_one()
        assert row.supersedes_hint is None
    finally:
        await _cleanup(session, owner)
