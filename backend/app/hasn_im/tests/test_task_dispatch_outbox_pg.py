"""任务执行派发 outbox 的真实 PostgreSQL + Redis 测试。"""

from __future__ import annotations

import json
import time
import uuid

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.routing import Route

from backend.app.hasn.service.task_scheduler import TaskSchedulerService
from backend.app.hasn_im.adapters.routing.node_session_service import NodeSessionService
from backend.app.hasn_im.adapters.routing.redis_presence_store import OFFLINE_PREFIX
from backend.app.hasn_im.application.provider import get_realtime_gateway
from backend.app.hasn_im.observability.offline_shadow_reconciler import (
    collect_offline_shadow_report,
)
from backend.app.hasn_task.model.run import HasnTaskRun
from backend.app.hasn_task.model.skill_bundle import HasnSkillBundle
from backend.app.hasn_task.model.task import HasnTask
from backend.app.hasn_task.model.task_dispatch_outbox import TaskDispatchOutbox
from backend.app.hasn_task.service.task_dispatch_outbox import (
    build_task_dispatch_relay,
    enqueue_task_exec,
)
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.redis import redis_client
from backend.database.schema_names import SCHEMA_NAMES

pytestmark = pytest.mark.asyncio(loop_scope='session')
_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')


@pytest_asyncio.fixture(loop_scope='session')
async def task_env() -> AsyncIterator[dict[str, Any]]:
    """创建唯一任务数据，并在用例结束后只清理该任务和对应 Redis 队列。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    owner_id = f'h_task_outbox_{suffix}'
    agent_id = f'a_task_outbox_{suffix}'
    offline_key = f'{OFFLINE_PREFIX}:{owner_id}'
    await redis_client.delete(offline_key)

    now = datetime.now(timezone.utc)
    bundle_name = f'backend-dev-{suffix}'
    async with session_factory.begin() as db:
        previous_task = HasnTask(
            owner_id=owner_id,
            agent_id=agent_id,
            name=f'上一次任务{suffix}',
            prompt='上一次任务',
            schedule_type='once',
            schedule_config={},
            enabled=False,
            state='completed',
        )
        db.add(previous_task)
        await db.flush()
        db.add(
            HasnTaskRun(
                task_id=previous_task.id,
                agent_id=agent_id,
                status='success',
                output='上次真实执行结果',
                prompt_snapshot='上一次任务',
            )
        )
        db.add(
            HasnSkillBundle(
                owner_id=owner_id,
                name=bundle_name,
                display_name='后端开发',
                description='真实 Skill Bundle',
                skill_ids=['pytest', 'test-driven-development'],
                instruction='先运行后端测试，再汇报结果。',
            )
        )
        task = HasnTask(
            owner_id=owner_id,
            agent_id=agent_id,
            name=f'事务派发测试{suffix}',
            prompt='生成一次真实任务派发',
            skill_bundle_ids=[bundle_name],
            skill_ids=['test-driven-development'],
            enabled_toolsets=['terminal'],
            context_from_task_id=previous_task.id,
            schedule_type='once',
            schedule_config={'run_at': now.isoformat()},
            enabled=True,
            state='scheduled',
            next_run_at=now,
        )
        db.add(task)
        await db.flush()
        task_id = task.id

    try:
        yield {
            'session_factory': session_factory,
            'task_id': task_id,
            'owner_id': owner_id,
            'agent_id': agent_id,
            'offline_key': offline_key,
            'now': now,
            'bundle_name': bundle_name,
        }
    finally:
        await redis_client.delete(offline_key)
        async with session_factory.begin() as db:
            await db.execute(
                sa.text(f'DELETE FROM {_SYNC_EVENTS} WHERE owner_id = :owner_id'),
                {'owner_id': owner_id},
            )
            await db.execute(
                sa.text('DELETE FROM hasn_task.task_dispatch_outbox WHERE task_id = :task_id'),
                {'task_id': task_id},
            )
            await db.execute(
                sa.text(
                    'DELETE FROM hasn_task.run WHERE task_id IN ('
                    'SELECT id FROM hasn_task.task WHERE owner_id = :owner_id)'
                ),
                {'owner_id': owner_id},
            )
            await db.execute(
                sa.text('DELETE FROM hasn_task.task WHERE owner_id = :owner_id'),
                {'owner_id': owner_id},
            )
            await db.execute(
                sa.text('DELETE FROM hasn_task.skill_bundle WHERE owner_id = :owner_id'),
                {'owner_id': owner_id},
            )
        await engine.dispose()


async def _load_task(db: AsyncSession, task_id: int) -> HasnTask:
    """从新会话读取任务，避免回滚后的 ORM 状态污染后续断言。"""
    return (await db.execute(select(HasnTask).where(HasnTask.id == task_id))).scalar_one()


async def test_task_run_and_dispatch_command_share_one_transaction(
    task_env: dict[str, Any],
) -> None:
    """业务事务失败时，任务推进、run 和派发命令必须一起回滚。"""
    scheduler = TaskSchedulerService()
    session_factory = task_env['session_factory']

    with pytest.raises(RuntimeError, match='故障注入'):
        async with session_factory.begin() as db:
            task = await _load_task(db, task_env['task_id'])
            await scheduler._dispatch_task(db, task, task_env['now'])
            raise RuntimeError('故障注入：业务提交前中止')

    async with session_factory() as db:
        task = await _load_task(db, task_env['task_id'])
        run_count = int(
            (
                await db.execute(select(sa.func.count()).select_from(HasnTaskRun).where(HasnTaskRun.task_id == task.id))
            ).scalar_one()
        )
        outbox_count = int(
            (
                await db.execute(
                    select(sa.func.count()).select_from(TaskDispatchOutbox).where(TaskDispatchOutbox.task_id == task.id)
                )
            ).scalar_one()
        )
        sync_event_count = int(
            (
                await db.execute(
                    sa.text(
                        f"SELECT count(*) FROM {_SYNC_EVENTS} WHERE owner_id = :owner_id AND event_type = 'task.exec'"
                    ),
                    {'owner_id': task_env['owner_id']},
                )
            ).scalar_one()
        )
    assert task.state == 'scheduled'
    assert task.run_count == 0
    assert run_count == 0
    assert outbox_count == 0
    assert sync_event_count == 0
    assert await redis_client.llen(task_env['offline_key']) == 0


async def test_committed_task_dispatch_recovers_via_real_redis(
    task_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交后 relay 可恢复投递，且主人节点收到稳定 run 派生的真实协议帧。"""
    scheduler = TaskSchedulerService()
    session_factory = task_env['session_factory']

    async with session_factory.begin() as db:
        task = await _load_task(db, task_env['task_id'])
        await scheduler._dispatch_task(db, task, task_env['now'])

    async with session_factory() as db:
        run = (await db.execute(select(HasnTaskRun).where(HasnTaskRun.task_id == task_env['task_id']))).scalar_one()
        outbox = (
            await db.execute(select(TaskDispatchOutbox).where(TaskDispatchOutbox.task_id == task_env['task_id']))
        ).scalar_one()
        payload = dict(outbox.payload)
        sync_events = (
            (
                await db.execute(
                    sa.text(
                        'SELECT hasn_id, aggregate_type, aggregate_id, payload, '
                        'producer, source_event_id '
                        f'FROM {_SYNC_EVENTS} '
                        "WHERE owner_id = :owner_id AND event_type = 'task.exec'"
                    ),
                    {'owner_id': task_env['owner_id']},
                )
            )
            .mappings()
            .all()
        )

    assert outbox.status == 'pending'
    assert outbox.run_id == run.id
    assert outbox.target_owner_id == task_env['owner_id']
    assert payload['agent_id'] == task_env['agent_id']
    assert payload['run_id'] == run.id
    assert payload['session_id'] == f'sess_task_{run.id}'
    assert payload['dispatch_id'] == f'task:run:{run.id}:exec'
    assert payload['context'] == {'previous_output': '上次真实执行结果'}
    assert payload['skill_bundles'] == [task_env['bundle_name']]
    assert payload['skill_bundle_definitions'] == [
        {
            'name': task_env['bundle_name'],
            'display_name': '后端开发',
            'description': '真实 Skill Bundle',
            'skill_ids': ['pytest', 'test-driven-development'],
            'instruction': '先运行后端测试，再汇报结果。',
        }
    ]
    assert payload['skills'] == ['test-driven-development']
    assert payload['enabled_toolsets'] == ['terminal']
    assert sync_events == [
        {
            'hasn_id': task_env['agent_id'],
            'aggregate_type': 'task_run',
            'aggregate_id': str(run.id),
            'payload': payload,
            'producer': 'hasn_task',
            'source_event_id': payload['dispatch_id'],
        }
    ]
    assert await redis_client.llen(task_env['offline_key']) == 0

    # 同 run、同 payload 重试必须返回原命令；同键不同载荷必须显式冲突。
    async with session_factory.begin() as db:
        duplicate_id = await enqueue_task_exec(
            db,
            run_id=run.id,
            task_id=task_env['task_id'],
            target_owner_id=task_env['owner_id'],
            payload=payload,
        )
        assert duplicate_id == outbox.command_id
        conflicting = {**payload, 'prompt': '冲突载荷'}
        with pytest.raises(ValueError, match='幂等键冲突'):
            await enqueue_task_exec(
                db,
                run_id=run.id,
                task_id=task_env['task_id'],
                target_owner_id=task_env['owner_id'],
                payload=conflicting,
            )
        duplicate_sync_count = int(
            (
                await db.execute(
                    sa.text(
                        f"SELECT count(*) FROM {_SYNC_EVENTS} WHERE owner_id = :owner_id AND event_type = 'task.exec'"
                    ),
                    {'owner_id': task_env['owner_id']},
                )
            ).scalar_one()
        )
        assert duplicate_sync_count == 1

    relay = build_task_dispatch_relay(
        session_factory=session_factory,
        gateway=get_realtime_gateway(),
        instance_id=f'test-task-relay-{uuid.uuid4().hex}',
    )
    # 数据库 now() 保留微秒，而 relay 使用整数秒；推进一秒消除同秒边界。
    stats = await relay.drain_once(now=int(time.time()) + 1)
    assert stats.claimed == 1
    assert stats.completed == 1

    raw_frames = await redis_client.lrange(task_env['offline_key'], 0, -1)
    assert len(raw_frames) == 1
    frame = json.loads(raw_frames[0])
    assert frame == {
        'hasn': 'hasn/0.2',
        'method': 'hasn.task.exec',
        'params': payload,
    }
    monkeypatch.setattr(settings, 'HASN_OFFLINE_RECOVERY', 'dual')
    node_session = NodeSessionService()
    messages, claims = await node_session.claim_offline_messages([task_env['owner_id']])
    assert messages == []
    assert claims == {}
    assert await node_session.get_offline_messages([task_env['owner_id']]) == []
    assert await redis_client.llen(task_env['offline_key']) == 1
    async with session_factory() as db:
        shadow_report = await collect_offline_shadow_report(
            db,
            offline_keys=[task_env['offline_key']],
            now=task_env['now'],
            publish_metrics=False,
        )
    assert shadow_report.both == 1
    assert shadow_report.redis_only == 0
    assert shadow_report.sync_only == 0
    assert shadow_report.redis_only_unrecoverable == 0

    async with session_factory() as db:
        completed = (
            await db.execute(select(TaskDispatchOutbox).where(TaskDispatchOutbox.command_id == outbox.command_id))
        ).scalar_one()
    assert completed.status == 'completed'
    assert completed.completed_at is not None


def test_task_outbox_model_uses_private_schema_and_has_no_generic_routes() -> None:
    """内部队列映射到 hasn_task，且不暴露 admin/app/agent/open 通用 CRUD。"""
    from backend.app.hasn_task.api.router import agent, app

    assert TaskDispatchOutbox.__table__.schema == 'hasn_task'
    assert all('dispatch/outbox' not in route.path for route in app.routes if isinstance(route, Route))
    assert all('dispatch/outbox' not in route.path for route in agent.routes if isinstance(route, Route))
