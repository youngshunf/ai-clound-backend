"""sync inbox 与业务库之间的独立两事务 worker。

由 Supervisor 单独管理，不挂在 API lifespan：

- ``run``：常驻短轮询，TERM/INT 到达后完成当前业务事务再退出；
- ``probe``：输出数据库角色与各状态计数；未决 dead 行使探针失败；
- 业务提交与 sync ACK 之间采用 receipt 幂等保护，支持崩溃后至少一次重放。
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
import os
import signal
import socket
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn.service.hasn_sync_business_receipts_service import (
    hasn_sync_business_receipts_service,
)
from backend.app.hasn.service.sync_business_handlers import (
    PermanentSyncBusinessError,
    SyncBusinessHandler,
    build_sync_handler_registry,
)
from backend.app.hasn_sync.adapters.sqlalchemy_store import SQLAlchemySyncStore
from backend.common.log import log
from backend.database.db import (
    python_backend_db_session,
    sync_service_db_session,
)
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone


_POLL_SECONDS = 0.5
_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


@dataclass(slots=True)
class SyncInboxWorker:
    """先提交领取，再提交业务写，最后提交 ACK；业务 handler 必须幂等。"""

    sync_session_factory: async_sessionmaker[AsyncSession] = sync_service_db_session
    business_session_factory: async_sessionmaker[AsyncSession] = (
        python_backend_db_session
    )
    registry: Mapping[str, SyncBusinessHandler] = field(
        default_factory=build_sync_handler_registry
    )
    instance_id: str = field(
        default_factory=lambda: f'sync-worker-{uuid.uuid4().hex[:16]}'
    )
    lease_seconds: int = 60
    max_attempts: int = 8
    store: SQLAlchemySyncStore = field(default_factory=SQLAlchemySyncStore)

    async def process_one(self, *, now: datetime | None = None) -> bool:
        """处理至多一条事件；没有可领取事件时返回 False。"""
        current_time = now or timezone.now()
        async with self.sync_session_factory.begin() as sync_db:
            claimed = await self.store.claim_inbox(
                sync_db,
                instance_id=self.instance_id,
                now=current_time,
                lease_seconds=self.lease_seconds,
                event_types=tuple(self.registry),
            )
        if claimed is None:
            return False

        try:
            handler = self.registry.get(claimed.envelope.event_type)
            if handler is None:
                raise ValueError(
                    f'未注册 sync inbox 业务 handler：{claimed.envelope.event_type}'
                )
            async with self.business_session_factory.begin() as business_db:
                first_application = (
                    await hasn_sync_business_receipts_service.reserve(
                        db=business_db,
                        idempotency_key=claimed.idempotency_key,
                        owner_id=claimed.envelope.owner_id,
                        node_id=claimed.envelope.node_id,
                        client_event_id=claimed.envelope.client_event_id,
                        event_type=claimed.envelope.event_type,
                    )
                )
                if first_application:
                    await handler.apply(
                        business_db,
                        claimed.envelope,
                        idempotency_key=claimed.idempotency_key,
                    )
        except Exception as exc:
            failed_at = now or timezone.now()
            max_attempts = (
                claimed.attempt_count
                if isinstance(exc, PermanentSyncBusinessError)
                else self.max_attempts
            )
            async with self.sync_session_factory.begin() as sync_db:
                status = await self.store.mark_inbox_failed(
                    sync_db,
                    claimed,
                    error=str(exc),
                    now=failed_at,
                    max_attempts=max_attempts,
                )
            message = (
                f'sync inbox 业务应用失败：row_id={claimed.row_id} '
                f'event_type={claimed.envelope.event_type} '
                f'attempt={claimed.attempt_count} status={status} error={exc}'
            )
            if status == 'dead':
                log.error(message)
            else:
                log.warning(message)
            return True

        async with self.sync_session_factory.begin() as sync_db:
            await self.store.mark_inbox_applied(
                sync_db,
                claimed,
                now=now or timezone.now(),
            )
        return True


@dataclass(frozen=True)
class SyncWorkerProbe:
    """sync worker 的机器可读探针。"""

    healthy: bool
    database_role: str
    accepted: int
    processing: int
    retry: int
    applied: int
    dead: int
    conflict: int
    ignored_accepted: int

    @property
    def pending(self) -> int:
        """兼容运维口径：尚未领取的 accepted 数量。"""
        return self.accepted


async def collect_probe(
    session_factory: async_sessionmaker[AsyncSession] = sync_service_db_session,
) -> SyncWorkerProbe:
    """从真实 sync 表读取角色与可处理事件的状态计数。"""
    event_types = tuple(build_sync_handler_registry())
    async with session_factory() as db:
        database_role = str(
            (await db.execute(sa.text('SELECT current_user'))).scalar_one()
        )
        rows = (
            await db.execute(
                sa.text(
                    'SELECT status, count(*) AS count '
                    f'FROM {_INBOX} '  # noqa: S608 内部常量表名
                    'WHERE event_type = ANY(CAST(:event_types AS text[])) '
                    'GROUP BY status'
                ),
                {'event_types': list(event_types)},
            )
        ).mappings()
        ignored_accepted = int(
            (
                await db.execute(
                    sa.text(
                        f'SELECT count(*) FROM {_INBOX} '  # noqa: S608 内部常量表名
                        "WHERE status = 'accepted' "
                        'AND NOT (event_type = ANY(CAST(:event_types AS text[])))'
                    ),
                    {'event_types': list(event_types)},
                )
            ).scalar_one()
        )
    counts = {str(row.status): int(row.count) for row in rows}
    return SyncWorkerProbe(
        healthy=counts.get('dead', 0) == 0,
        database_role=database_role,
        accepted=counts.get('accepted', 0),
        processing=counts.get('processing', 0),
        retry=counts.get('retry', 0),
        applied=counts.get('applied', 0),
        dead=counts.get('dead', 0),
        conflict=counts.get('conflict', 0),
        ignored_accepted=ignored_accepted,
    )


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """把 TERM/INT 转为协作式停止，不取消正在执行的业务事务。"""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(
                signum,
                lambda *_args: loop.call_soon_threadsafe(stop_event.set),
            )


async def run_forever(
    *,
    stop_event: asyncio.Event | None = None,
    poll_seconds: float = _POLL_SECONDS,
) -> None:
    """持续处理 inbox；空队列短暂等待，收到停止信号后优雅退出。"""
    resolved_stop = stop_event or asyncio.Event()
    if stop_event is None:
        _install_signal_handlers(resolved_stop)
    worker = SyncInboxWorker(
        instance_id=f'{socket.gethostname()}:{os.getpid()}'
    )
    log.info('sync inbox worker 进程启动（instance=%s）', worker.instance_id)
    try:
        while not resolved_stop.is_set():
            try:
                processed = await worker.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('sync inbox worker 轮询失败')
                processed = False
            if processed or resolved_stop.is_set():
                continue
            try:
                await asyncio.wait_for(
                    resolved_stop.wait(),
                    timeout=poll_seconds,
                )
            except TimeoutError:
                pass
    finally:
        log.info(
            'sync inbox worker 进程已优雅停止（instance=%s）',
            worker.instance_id,
        )


async def _run_probe() -> int:
    """执行探针并输出单行 JSON。"""
    try:
        probe = await collect_probe()
    except Exception:
        log.exception('sync inbox worker 探针连接数据库失败')
        return 2
    payload = asdict(probe)
    payload['pending'] = probe.pending
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if probe.healthy else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='唤星云端 sync inbox worker')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('run', help='启动常驻 worker')
    subparsers.add_parser('probe', help='输出健康与状态计数')
    return parser.parse_args()


def main() -> int:
    """命令行入口。"""
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    args = _parse_args()
    if args.command == 'probe':
        return asyncio.run(_run_probe())
    asyncio.run(run_forever())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
