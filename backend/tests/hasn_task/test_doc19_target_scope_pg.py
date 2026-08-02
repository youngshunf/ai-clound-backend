"""doc19 S4：内置任务 target_scope 广播语义 + memory_review 条目真实 PG 验收（零 mock，末尾回滚）。

设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §9 / 决策 D-24

核心心智：`hasn_task.task` 上有 uq_task_owner_builtin_key (owner_id, builtin_key) 唯一索引，
一个 owner 同一 builtin_key 只能有一行存活；播种幂等键 `bts_{owner}_{builtin_key}` 同样是
per-(owner,builtin_key) 唯一。所以「广播给全部分身」在云端**不能**靠多播几行任务实现——云端仍只播
一行（绑主脑），把 `target_scope` 透传到任务行并随普通 task 同步事件下行，由各节点 task_scheduler
在 `target_scope='all_agents'` 时向本节点每个在线分身各派发一次（本地扇出）。

覆盖：
- catalog 新列默认 master_brain；CHECK 约束拒绝非法取值。
- memory_review 条目：all_agents + 每日 03:00 cron + 默认启用 + target_agent_type NULL（绑主脑）。
- 播种后 owner 的 task 行 target_scope='all_agents' 且绑主脑；同 owner 同 builtin_key 仍只有一行。
- **透传链路钉死**：同步下行事件 payload 必须带 target_scope（本地扇出的唯一依据，漏带即退化）。
- refresh-builtin（§6.6 手动更新）后 target_scope 仍正确，并随官方目录定义变化。

需要本地 PostgreSQL :15432，且 2026-07-31 三个迁移（catalog/task 增列 + memory_review 种子）已应用。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.hasn_auth import register_hasn_agent
from backend.app.hasn.service.hasn_sync_service import hasn_sync_service
from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.service.builtin_seeding_service import (
    reconcile_builtin_agents,
    seed_builtin_tasks,
    update_builtin_task_from_catalog,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

MEMORY_REVIEW = 'memory_review'


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


async def _make_owner(session) -> str:
    tag = uuid.uuid4().hex[:8]
    owner = f'h_ts_{tag}'
    uid = 5_100_000_000 + int(uuid.uuid4().int % 1_000_000_000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{uid}', user_id=uid, nickname='TSOwner', status='active'))
    await session.flush()
    return owner


async def _bootstrap_owner_agents(session, owner: str) -> None:
    """模拟 onboarding：建主脑 assistant + reconcile 建齐专业内置分身。"""
    await register_hasn_agent(
        db=session,
        owner_hasn_id=owner,
        agent_name='assistant',
        display_name=f'主脑_{owner}',
        role='primary',
        builtin_agent_key='assistant',
        agent_type='cloud',
        template_id='huanxing/agent/assistant',
        created_via='onboarding',
    )
    await reconcile_builtin_agents(session, owner_id=owner)


async def _primary_agent_id(session, owner: str) -> str:
    return (
        await session.execute(
            select(HasnAgents.hasn_id).where(
                HasnAgents.owner_id == owner,
                HasnAgents.builtin_agent_key == 'assistant',
            )
        )
    ).scalar_one()


async def test_catalog_target_scope_default_and_check(session) -> None:
    """catalog 新列：默认 master_brain（存量条目零行为变化）；CHECK 拒绝非法取值。"""
    # 存量条目（除 memory_review 外）全部默认 master_brain，既有单绑语义不变
    rows = (
        await session.execute(
            select(HasnBuiltinTaskCatalog.builtin_key, HasnBuiltinTaskCatalog.target_scope).where(
                HasnBuiltinTaskCatalog.builtin_key != MEMORY_REVIEW
            )
        )
    ).all()
    assert rows, '目录应有存量条目'
    assert all(r.target_scope == 'master_brain' for r in rows), (
        f'存量条目必须默认 master_brain（不得改变既有单绑语义）：{[(r.builtin_key, r.target_scope) for r in rows]}'
    )

    # 不显式给 target_scope 时落列默认值
    probe_key = f'ts_probe_{uuid.uuid4().hex[:8]}'
    async with session.begin_nested():
        await session.execute(
            sa.text(
                'INSERT INTO hasn_task.builtin_catalog (builtin_key, name, revision) '
                'VALUES (:k, :n, 1)'
            ),
            {'k': probe_key, 'n': '探针条目'},
        )
        scope = (
            await session.execute(
                sa.text('SELECT target_scope FROM hasn_task.builtin_catalog WHERE builtin_key = :k'),
                {'k': probe_key},
            )
        ).scalar_one()
        assert scope == 'master_brain', f'新条目未给 target_scope 时应落默认 master_brain，实得 {scope}'

    # CHECK：非法取值必须被数据库挡住（防止拼错的 scope 静默变成「谁都不派」或「全派」）
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa.text(
                    'INSERT INTO hasn_task.builtin_catalog (builtin_key, name, revision, target_scope) '
                    "VALUES (:k, :n, 1, 'everyone')"
                ),
                {'k': f'ts_bad_{uuid.uuid4().hex[:8]}', 'n': '非法广播语义'},
            )


async def test_memory_review_catalog_entry(session) -> None:
    """memory_review 目录条目：all_agents + 每日 03:00 + 默认启用 + 绑主脑（target_agent_type NULL）。"""
    item = (
        await session.execute(
            select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == MEMORY_REVIEW)
        )
    ).scalar_one_or_none()
    assert item is not None, 'memory_review 条目缺失（2026-07-31-seed-builtin-memory-review.sql 未应用？）'
    assert item.target_scope == 'all_agents', '记忆复盘要求每个分身整理自己那一片，必须是 all_agents'
    assert item.target_agent_type is None, '记忆复盘与「哪类专家」无关，不绑内置 agent 类型'
    assert item.schedule_type == 'cron'
    assert item.schedule_config == {'expr': '0 3 * * *'}
    assert item.enabled is True and item.default_enabled is True, '记忆复盘是人人通用的基础动作，默认开'
    # system_prompt 必须教完整闭环并按是否主脑分叉（doc19 §9）
    prompt = item.system_prompt or ''
    for token in (
        'hasn.memory.review',
        'hasn.memory.update',
        'hasn.memory.supersede',
        'hasn.memory.withdraw',
        'hasn.memory.merge',
        'supersedes_hint',
        '主脑',
    ):
        assert token in prompt, f'system_prompt 缺少闭环要件：{token}'
    assert '\\n' not in prompt, 'system_prompt 混进了字面量反斜杠 n（种子 SQL 的字符串续行写法错误）'


async def test_catalog_listing_exposes_seeding_semantics(session) -> None:
    """目录拉取响应必须带播种语义三列（default_enabled / target_agent_type / target_scope）。

    这三列早已在表里，却一直漏在 BuiltinTaskItem 之外（既存 schema 漂移）：拉目录的一方看不到
    「默认开不开、绑哪类分身、要不要扇出」，只能靠云端播种时的隐式行为。随 doc19 §9 一并补齐。
    """
    from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service

    resp = await workbench_builtin_task_service.list_enabled(session)
    items = {it.builtin_key: it for it in resp.items}
    assert MEMORY_REVIEW in items, 'memory_review 为 enabled=TRUE，应出现在目录拉取响应中'
    dumped = items[MEMORY_REVIEW].model_dump()
    assert dumped['target_scope'] == 'all_agents'
    assert dumped['default_enabled'] is True
    assert dumped['target_agent_type'] is None
    assert items['daily_briefing'].target_scope == 'master_brain'


async def test_seeded_task_carries_all_agents_and_binds_primary(session) -> None:
    """播种：memory_review 任务行 target_scope='all_agents' 且绑主脑；云端仍只一行（唯一索引所限）。"""
    owner = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    seeded = await seed_builtin_tasks(session, owner_id=owner)
    assert MEMORY_REVIEW in seeded, f'memory_review 应被播种，实得 {seeded}'

    tasks = (
        (
            await session.execute(
                select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == MEMORY_REVIEW)
            )
        )
        .scalars()
        .all()
    )
    assert len(tasks) == 1, f'受 uq_task_owner_builtin_key 约束，云端只能播一行，实得 {len(tasks)} 行'
    task = tasks[0]
    assert task.target_scope == 'all_agents', 'catalog 的广播语义必须透传进任务行'
    assert task.agent_id == await _primary_agent_id(session, owner), '云端那一行仍绑主脑（真正的扇出在本地）'
    assert task.created_by_kind == 'builtin'
    assert task.enabled is True, 'default_enabled=TRUE → 首播即启用'

    # 回归：普通内置任务（daily_briefing）保持 master_brain，既有单绑行为零变化
    briefing = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == 'daily_briefing')
        )
    ).scalar_one()
    assert briefing.target_scope == 'master_brain', '未声明广播语义的内置任务必须保持只派绑定分身'


async def test_sync_event_payload_carries_target_scope(session) -> None:
    """透传链路钉死：下行同步事件 payload 必须带 target_scope——本地扇出完全依赖这个键。"""
    owner = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)

    events = await hasn_sync_service.gateway.pull_task_events(
        session, owner_id=owner, node_id='n_deviceMem', after_revision=0, limit=300
    )
    by_key = {
        e.payload.get('builtin_key'): e.payload for e in events if e.payload.get('created_by_kind') == 'builtin'
    }
    assert MEMORY_REVIEW in by_key, f'memory_review 应广播到 owner 名下节点，实得 {sorted(k for k in by_key if k)}'
    assert by_key[MEMORY_REVIEW].get('target_scope') == 'all_agents', (
        '下行 payload 缺 target_scope=all_agents，本地 task_scheduler 无从扇出（记忆复盘退化成只有主脑跑）'
    )
    # 非广播条目也必须显式带值，避免本地把「缺字段」当成未知语义
    assert by_key['daily_briefing'].get('target_scope') == 'master_brain'


async def test_all_agents_run_summary_accepts_each_executing_agent(session) -> None:
    """广播任务的每条本地 run 都由实际执行分身上报，不能只允许云端任务行绑定的主脑。"""
    owner = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)
    task = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == MEMORY_REVIEW)
        )
    ).scalar_one()
    peer_agent = (
        await session.execute(
            select(HasnAgents.hasn_id)
            .where(HasnAgents.owner_id == owner, HasnAgents.hasn_id != task.agent_id)
            .order_by(HasnAgents.hasn_id)
            .limit(1)
        )
    ).scalar_one()
    run_uuid = f'run_{uuid.uuid4().hex}'

    stored = await hasn_sync_service.gateway.save_task_run_summary(
        session,
        owner_id=owner,
        agent_hasn_id=peer_agent,
        summary={
            'run_uuid': run_uuid,
            'task_uuid': task.task_uuid,
            'owner_id': owner,
            'agent_id': peer_agent,
            'executor_node_id': 'n_doc19_broadcast',
            'session_id': f'sess_{uuid.uuid4().hex}',
            'scheduled_fire_at': None,
            'dedupe_key': f'doc19-broadcast:{run_uuid}',
            'status': 'success',
            'output_summary': '本分身已完成本机记忆复盘',
            'error': None,
            'deep_link': None,
            'model': None,
            'token_usage': None,
            'duration_ms': 10,
            'started_at': None,
            'finished_at': None,
        },
    )

    assert stored['agent_id'] == peer_agent
    assert stored['task_uuid'] == task.task_uuid


async def test_master_brain_run_summary_still_rejects_other_agent(session) -> None:
    """非广播任务继续只认任务绑定分身，不能因广播修复而扩大普通任务权限。"""
    owner = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)
    task = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == 'daily_briefing')
        )
    ).scalar_one()
    peer_agent = (
        await session.execute(
            select(HasnAgents.hasn_id)
            .where(HasnAgents.owner_id == owner, HasnAgents.hasn_id != task.agent_id)
            .order_by(HasnAgents.hasn_id)
            .limit(1)
        )
    ).scalar_one()

    with pytest.raises(errors.ForbiddenError):
        await hasn_sync_service.gateway.save_task_run_summary(
            session,
            owner_id=owner,
            agent_hasn_id=peer_agent,
            summary={
                'run_uuid': f'run_{uuid.uuid4().hex}',
                'task_uuid': task.task_uuid,
            },
        )


async def test_refresh_builtin_keeps_target_scope(session) -> None:
    """§6.6 手动更新：target_scope 属官方定义，refresh 后保持正确并随目录变化。"""
    owner = await _make_owner(session)
    await _bootstrap_owner_agents(session, owner)
    await seed_builtin_tasks(session, owner_id=owner)

    task = (
        await session.execute(
            select(HasnTask).where(HasnTask.owner_id == owner, HasnTask.builtin_key == MEMORY_REVIEW)
        )
    ).scalar_one()
    item = (
        await session.execute(
            select(HasnBuiltinTaskCatalog).where(HasnBuiltinTaskCatalog.builtin_key == MEMORY_REVIEW)
        )
    ).scalar_one()

    # 官方只改名并抬 revision：refresh 后广播语义不得丢
    item.revision = (item.revision or 0) + 1
    item.name = '记忆复盘整理 v2'
    await session.flush()
    updated = await update_builtin_task_from_catalog(session, owner_id=owner, task_uuid=task.task_uuid)
    assert updated.name == '记忆复盘整理 v2'
    assert updated.target_scope == 'all_agents', 'refresh 不得把广播语义抹回 master_brain'

    # 官方改广播语义：refresh 后必须跟随（target_scope 是官方定义，不是用户偏好）
    item.revision = (item.revision or 0) + 1
    item.target_scope = 'master_brain'
    await session.flush()
    updated2 = await update_builtin_task_from_catalog(session, owner_id=owner, task_uuid=task.task_uuid)
    assert updated2.target_scope == 'master_brain', 'refresh 应应用官方最新广播语义'

    # 下行事件同样带上更新后的语义
    events = await hasn_sync_service.gateway.pull_task_events(
        session, owner_id=owner, node_id='n_deviceMem2', after_revision=0, limit=300
    )
    scopes = [
        e.payload.get('target_scope')
        for e in events
        if e.payload.get('builtin_key') == MEMORY_REVIEW and e.event_type == 'task.updated'
    ]
    assert scopes and scopes[-1] == 'master_brain', f'更新事件应带最新 target_scope，实得 {scopes}'
