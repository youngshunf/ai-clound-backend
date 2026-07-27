"""云端 IM 集成事件消费者独立进程。

由 Supervisor 单独管理，不挂在 API lifespan。支持：

- ``run``：常驻短轮询，TERM/INT 到达后完成当前 tick 再退出；
- ``probe``：输出 head、各消费者 cursor/lag、失败与未决 dead letter 的 JSON；
- ``probe --max-lag N``：作为就绪探针，durable DLQ 或超阈值 lag 时返回非零。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.consumers import store
from backend.app.hasn_im.consumers.framework import ConsumerRunner
from backend.app.hasn_im.consumers.push_notifier import PushNotifier
from backend.app.hasn_im.consumers.realtime_notifier import RealtimeNotifier
from backend.app.hasn_im.consumers.sync_projector import SyncProjector
from backend.app.hasn_im.application.provider import (
    get_im_gateway,
    get_realtime_gateway,
    get_relation_gateway,
)
from backend.app.hasn_im.observability import metrics
from backend.app.hasn.service.session_im_outbox import build_session_im_relay
from backend.app.hasn.service.group_im_outbox import build_group_im_relay
from backend.app.hasn.service.hasn_relation_command_outbox_service import (
    RelationCommandOutboxRelay,
)
from backend.app.notification.service.notification_im_outbox import (
    build_notification_im_relay,
)
from backend.app.hasn_community.service.community_im_outbox import (
    build_community_im_relay,
)
from backend.app.hasn_task.service.task_dispatch_outbox import (
    build_task_dispatch_relay,
)
from backend.database.db import im_service_db_session, python_backend_db_session
from backend.database.schema_names import SCHEMA_NAMES

log = logging.getLogger(__name__)
_POLL_SECONDS = 0.5
_CONSUMER_NAMES = ('sync_projector', 'realtime_notifier', 'push_notifier')
_DURABLE_CONSUMERS = frozenset({'sync_projector'})
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OFFSETS = SCHEMA_NAMES.im_event_table('event_consumer_offsets')
_FAILURES = SCHEMA_NAMES.im_event_table('event_consumer_failures')


@dataclass(frozen=True)
class ConsumerProbe:
    """单消费者的可观测状态。"""

    consumer: str
    cursor: int
    lag: int
    failures: int
    unresolved_dead_letters: int
    lease_owner: str | None
    lease_until: str | None


@dataclass(frozen=True)
class ProducerOutboxProbe:
    """单个生产方消息 outbox 的可观测状态。"""

    producer: str
    pending: int
    processing: int
    dead_letters: int


@dataclass(frozen=True)
class WorkerProbe:
    """worker 探针输出。"""

    healthy: bool
    database_role: str
    event_head: int
    consumers: list[ConsumerProbe]
    producer_outboxes: list[ProducerOutboxProbe]


def build_runners(*, instance_id: str) -> list[ConsumerRunner]:
    """装配 R3 首批三个消费者，全部经 IM 受限角色读取事件和推进位点。"""
    consumers = (SyncProjector(), RealtimeNotifier(), PushNotifier())
    return [
        ConsumerRunner(
            consumer=consumer,
            sessionmaker=im_service_db_session,
            instance_id=instance_id,
        )
        for consumer in consumers
    ]


def build_producer_relays(*, instance_id: str) -> list[tuple[str, Any]]:
    """装配各生产方自有表对应的统一 relay。"""
    return [
        (
            'relation',
            RelationCommandOutboxRelay(
                session_factory=python_backend_db_session,
                relation_gateway=get_relation_gateway(),
            ),
        ),
        (
            'notification',
            build_notification_im_relay(
                session_factory=python_backend_db_session,
                gateway=get_im_gateway(),
                instance_id=instance_id,
            ),
        ),
        (
            'community',
            build_community_im_relay(
                session_factory=python_backend_db_session,
                gateway=get_im_gateway(),
                instance_id=instance_id,
            ),
        ),
        (
            'session',
            build_session_im_relay(
                session_factory=python_backend_db_session,
                gateway=get_im_gateway(),
                instance_id=instance_id,
            ),
        ),
        (
            'group',
            build_group_im_relay(
                session_factory=im_service_db_session,
                gateway=get_im_gateway(),
                instance_id=instance_id,
            ),
        ),
        (
            'task',
            build_task_dispatch_relay(
                session_factory=python_backend_db_session,
                gateway=get_realtime_gateway(),
                instance_id=instance_id,
            ),
        ),
    ]


async def collect_probe(
    session_factory: async_sessionmaker[AsyncSession] = im_service_db_session,
    producer_session_factory: async_sessionmaker[AsyncSession] = (
        python_backend_db_session
    ),
) -> WorkerProbe:
    """从真实 IM 表读取 head/cursor/failure/DLQ，并刷新低基数指标。"""
    async with session_factory() as db:
        database_role = str(
            (await db.execute(sa.text('SELECT current_user'))).scalar_one()
        )
        event_head = int(
            (
                await db.execute(
                    sa.text(
                        f'SELECT COALESCE(MAX(event_seq), 0) FROM {_EVENTS} '  # noqa: S608 内部常量表名
                        'WHERE shard_key = 0'
                    )
                )
            ).scalar_one()
            or 0
        )
        offset_rows = {
            str(row.consumer_name): row
            for row in (
                await db.execute(
                    sa.text(
                        'SELECT consumer_name, last_acked_seq, lease_owner, lease_until '
                        f'FROM {_OFFSETS} '  # noqa: S608 内部常量表名
                        'WHERE consumer_name = ANY(:consumer_names)'
                    ),
                    {'consumer_names': list(_CONSUMER_NAMES)},
                )
            ).mappings()
        }
        failure_rows = {
            str(row.consumer_name): row
            for row in (
                await db.execute(
                    sa.text(
                        'SELECT consumer_name, COUNT(*) AS failures, '
                        'COUNT(*) FILTER (WHERE dead_lettered_at IS NOT NULL '
                        'AND resolution IS NULL) AS unresolved_dead_letters '
                        f'FROM {_FAILURES} '  # noqa: S608 内部常量表名
                        'WHERE consumer_name = ANY(:consumer_names) '
                        'GROUP BY consumer_name'
                    ),
                    {'consumer_names': list(_CONSUMER_NAMES)},
                )
            ).mappings()
        }

    consumers: list[ConsumerProbe] = []
    for name in _CONSUMER_NAMES:
        offset = offset_rows.get(name)
        failure = failure_rows.get(name)
        cursor = int(offset.last_acked_seq) if offset else 0
        lag = max(0, event_head - cursor)
        consumers.append(
            ConsumerProbe(
                consumer=name,
                cursor=cursor,
                lag=lag,
                failures=int(failure.failures) if failure else 0,
                unresolved_dead_letters=(
                    int(failure.unresolved_dead_letters) if failure else 0
                ),
                lease_owner=str(offset.lease_owner) if offset and offset.lease_owner else None,
                lease_until=(
                    offset.lease_until.isoformat()
                    if offset and offset.lease_until
                    else None
                ),
            )
        )
        metrics.HASN_IM_CONSUMER_LAG.labels(consumer=name).set(lag)
    metrics.HASN_IM_INTEGRATION_EVENT_HEAD.set(event_head)
    healthy = not any(
        item.consumer in _DURABLE_CONSUMERS
        and item.unresolved_dead_letters > 0
        for item in consumers
    )
    producer_outboxes: list[ProducerOutboxProbe] = []
    producer_tables = (
        ('relation', 'public.hasn_relation_command_outbox'),
        ('notification', 'public.hasn_notification_im_command_outbox'),
        ('community', 'hasn_community.im_command_outbox'),
        ('session', 'public.hasn_session_im_command_outbox'),
        ('task', 'hasn_task.task_dispatch_outbox'),
    )
    async with producer_session_factory() as db:
        for producer, table_name in producer_tables:
            producer_counts = (
                await db.execute(
                    sa.text(
                        'SELECT status, count(*) AS count '
                        f'FROM {table_name} '  # noqa: S608 代码内固定表名
                        "WHERE status IN ('pending', 'processing', 'dead_letter') "
                        'GROUP BY status'
                    )
                )
            ).mappings().all()
            counts = {
                str(row.status): int(row.count) for row in producer_counts
            }
            producer_outboxes.append(
                ProducerOutboxProbe(
                    producer=producer,
                    pending=counts.get('pending', 0),
                    processing=counts.get('processing', 0),
                    dead_letters=counts.get('dead_letter', 0),
                )
            )
    async with session_factory() as db:
        group_counts = (
            await db.execute(
                sa.text(
                    'SELECT status, count(*) AS count '
                    'FROM public.hasn_group_im_command_outbox '
                    "WHERE status IN ('pending', 'processing', 'dead_letter') "
                    'GROUP BY status'
                )
            )
        ).mappings().all()
        counts = {str(row.status): int(row.count) for row in group_counts}
        producer_outboxes.append(
            ProducerOutboxProbe(
                producer='group',
                pending=counts.get('pending', 0),
                processing=counts.get('processing', 0),
                dead_letters=counts.get('dead_letter', 0),
            )
        )
    healthy = healthy and not any(
        item.dead_letters > 0 for item in producer_outboxes
    )
    return WorkerProbe(
        healthy=healthy,
        database_role=database_role,
        event_head=event_head,
        consumers=consumers,
        producer_outboxes=producer_outboxes,
    )


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """把 TERM/INT 转为协作式停止，不在事务中途取消 runner。"""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_args: loop.call_soon_threadsafe(stop_event.set))


async def run_forever(
    *,
    stop_event: asyncio.Event | None = None,
    poll_seconds: float = _POLL_SECONDS,
) -> None:
    """持续短轮询；收到停止信号后完成当前 runner 的事务再退出。"""
    instance_id = f'{socket.gethostname()}:{os.getpid()}'
    runners = build_runners(instance_id=instance_id)
    producer_relays = build_producer_relays(instance_id=instance_id)
    resolved_stop = stop_event or asyncio.Event()
    if stop_event is None:
        _install_signal_handlers(resolved_stop)
    log.info('IM 消费者进程启动（instance=%s）', instance_id)
    try:
        while not resolved_stop.is_set():
            for runner in runners:
                if resolved_stop.is_set():
                    break
                try:
                    stats = await runner.tick()
                    if stats.fetched:
                        log.info(
                            'IM 消费轮询完成（consumer=%s fetched=%d processed=%d '
                            'retried=%d dlq=%d best_effort_failed=%d）',
                            runner.name,
                            stats.fetched,
                            stats.processed,
                            stats.retried,
                            stats.dead_lettered,
                            stats.best_effort_failed,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception('IM 消费者轮询失败（consumer=%s）', runner.name)
            for producer, relay in producer_relays:
                if resolved_stop.is_set():
                    break
                try:
                    relay_stats = await relay.drain_once(now=int(time.time()))
                    if relay_stats.claimed:
                        log.info(
                            '生产 outbox 轮询完成（producer=%s claimed=%d '
                            'completed=%d retried=%d dlq=%d deduped=%d）',
                            producer,
                            relay_stats.claimed,
                            relay_stats.completed,
                            relay_stats.retried,
                            relay_stats.dead_lettered,
                            relay_stats.deduped,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception('生产 outbox 轮询失败（producer=%s）', producer)
            if resolved_stop.is_set():
                break
            try:
                await asyncio.wait_for(resolved_stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
    finally:
        async with im_service_db_session() as db:
            for runner in runners:
                await store.release_lease(
                    db,
                    consumer_name=runner.name,
                    owner=instance_id,
                )
            await db.commit()
        log.info('IM 消费者进程已优雅停止（instance=%s）', instance_id)


async def _run_probe(*, max_lag: int | None) -> int:
    """执行探针并输出机器可读 JSON。"""
    try:
        probe = await collect_probe()
    except Exception:
        log.exception('IM 消费者探针连接数据库失败')
        return 2
    payload: dict[str, Any] = asdict(probe)
    if max_lag is not None:
        lag_ok = all(item.lag <= max_lag for item in probe.consumers)
        payload['max_lag'] = max_lag
        payload['lag_ok'] = lag_ok
    else:
        lag_ok = True
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if probe.healthy and lag_ok else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='唤星云端 IM 消费者')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('run', help='启动常驻消费者')
    probe = subparsers.add_parser('probe', help='输出健康与 lag 状态')
    probe.add_argument('--max-lag', type=int, default=None, help='最大允许事件 lag')
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    args = _parse_args()
    if args.command == 'probe':
        return asyncio.run(_run_probe(max_lag=args.max_lag))
    asyncio.run(run_forever())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
