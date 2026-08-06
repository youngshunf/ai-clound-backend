"""doc19 S2 项目记忆真实 PG 验收（零 mock）。

设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md
  覆盖项目作用域、上下文可见性、supersedes_hint 和版本汇总

钉死的核心不变量：

1. **上行不改写作用域**：本地 hasn-node 选好的 `project` / `global` 作用域，云端
   唯一事实上行入口必须原样保留；云端不再提供直写或项目写继承入口；
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
from backend.tests.hasn_memory.fact_uplink_seed import seed_local_fact_uplink

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
# 一、上行保留本地已选定的作用域（§6.2）
# --------------------------------------------------------------------------------------


async def test_uplink_preserves_project_and_global_scopes(session: AsyncSession) -> None:
    """项目作用域由本地选定，云端上行只保留，不自行继承或改写。"""
    owner, agent, project = _owner(), _agent(), _project()
    try:
        project_fact = await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value='提交信息用中文',
            scope_kind='project',
            scope_id=project,
        )
        global_fact = await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='偏好',
            object_value='冰美式',
            scope_kind='global',
            scope_id=agent,
        )
        assert (project_fact['scope_kind'], project_fact['scope_id']) == (
            'project',
            project,
        )
        assert (global_fact['scope_kind'], global_fact['scope_id']) == (
            'global',
            agent,
        )
    finally:
        await _cleanup(session, owner)


async def test_world_subject_plus_project_scope_survives_uplink(session: AsyncSession) -> None:
    """§6.1/D-5：合法的 world + project 本地事实上行后原样保留。"""
    owner, agent, project = _owner(), _agent(), _project()
    try:
        fact = await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='world',
            subject_id=f'proj_{project[:20]}',
            predicate='技术决策',
            object_value='本项目数据库用 PostgreSQL 16',
            scope_kind='project',
            scope_id=project,
        )
        assert fact['subject_kind'] == 'world'
        assert fact['scope_kind'] == 'project'
        assert fact['scope_id'] == project
        assert fact['agent_id'] is None
    finally:
        await _cleanup(session, owner)


# --------------------------------------------------------------------------------------
# 二、并集检索：读不收窄（§6.2 + 铁律「项目轴写继承·读不收窄」）
# --------------------------------------------------------------------------------------


async def _seed_three_scopes(session: AsyncSession, owner: str, agent: str, marker: str) -> dict[str, str]:
    """造 3 条同关键词事实：当前项目 / 另一个项目 / 全局。返回 {档位: fact_id}。"""
    here = _project()
    elsewhere = _project()
    mine = await seed_local_fact_uplink(
        session,
        owner_id=owner,
        agent_id=agent,
        subject_kind='agent_self',
        predicate='部署方式',
        object_value=f'{marker}·本项目走 systemd',
        scope_kind='project',
        scope_id=here,
    )
    theirs = await seed_local_fact_uplink(
        session,
        owner_id=owner,
        agent_id=agent,
        subject_kind='agent_self',
        predicate='部署方式',
        object_value=f'{marker}·别的项目走 docker',
        scope_kind='project',
        scope_id=elsewhere,
    )
    globally = await seed_local_fact_uplink(
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
        world_fact = await seed_local_fact_uplink(
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
        out = await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value=marker,
            scope_kind='project',
            scope_id=project,
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
        await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='项目约定',
            object_value=marker,
            scope_kind='project',
            scope_id=project,
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
        old = await seed_local_fact_uplink(
            session,
            owner_id=owner,
            agent_id=agent,
            subject_kind='agent_self',
            predicate='住址',
            object_value=f'{marker}·老地址',
        )
        new = await seed_local_fact_uplink(
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
        out = await seed_local_fact_uplink(
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
