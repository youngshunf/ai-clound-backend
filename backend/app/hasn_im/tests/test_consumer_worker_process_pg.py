"""独立 IM consumer 进程、探针与 Supervisor 的真实回归。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_im.consumer_worker import collect_probe
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES

_CONSUMERS = ('sync_projector', 'realtime_notifier', 'push_notifier')
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OFFSETS = SCHEMA_NAMES.im_event_table('event_consumer_offsets')


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text('SELECT 1'))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 consumer 进程测试：{exc!r}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _snapshot_and_align_offsets(sessionmaker):
    """暂存生产消费者位点并临时对齐 head，避免进程测试重放共享开发数据。"""
    async with sessionmaker() as db:
        snapshot = list(
            (
                await db.execute(
                    sa.text(
                        'SELECT consumer_name, last_acked_seq, lease_owner, lease_until '
                        f'FROM {_OFFSETS} '  # noqa: S608 内部常量表名
                        'WHERE consumer_name = ANY(:names)'
                    ),
                    {'names': list(_CONSUMERS)},
                )
            )
            .mappings()
            .all()
        )
        head = int(
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
        for name in _CONSUMERS:
            await db.execute(
                sa.text(
                    f'INSERT INTO {_OFFSETS} '  # noqa: S608 内部常量表名
                    '(consumer_name, last_acked_seq, lease_owner, lease_until, updated_at) '
                    'VALUES (:name, :head, NULL, NULL, now()) '
                    'ON CONFLICT (consumer_name) DO UPDATE '
                    'SET last_acked_seq = :head, lease_owner = NULL, '
                    'lease_until = NULL, updated_at = now()'
                ),
                {'name': name, 'head': head},
            )
        await db.commit()
    return snapshot


async def _restore_offsets(sessionmaker, snapshot) -> None:
    """恢复进程测试前的位点，不污染其他测试。"""
    async with sessionmaker() as db:
        await db.execute(
            sa.text(
                f'DELETE FROM {_OFFSETS} '  # noqa: S608 内部常量表名
                'WHERE consumer_name = ANY(:names)'
            ),
            {'names': list(_CONSUMERS)},
        )
        for row in snapshot:
            await db.execute(
                sa.text(
                    f'INSERT INTO {_OFFSETS} '  # noqa: S608 内部常量表名
                    '(consumer_name, last_acked_seq, lease_owner, lease_until, updated_at) '
                    'VALUES (:name, :cursor, :owner, :until, now())'
                ),
                {
                    'name': row['consumer_name'],
                    'cursor': row['last_acked_seq'],
                    'owner': row['lease_owner'],
                    'until': row['lease_until'],
                },
            )
        await db.commit()


async def _start_and_term_worker() -> str:
    """启动真实子进程，发送 TERM 并返回日志。"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'backend.app.hasn_im.consumer_worker',
        'run',
        cwd=Path(__file__).resolve().parents[4],
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    captured: list[bytes] = []
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        if not line:
            raise AssertionError('consumer 在安装信号处理器前异常退出')
        captured.append(line)
        if 'IM 消费者进程启动' in line.decode('utf-8', errors='replace'):
            break
    process.terminate()
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0
    return b''.join([*captured, stdout]).decode('utf-8', errors='replace')


@pytest.mark.asyncio
async def test_real_process_term_restart_and_zero_lag(sessionmaker_pg) -> None:
    """真实进程两次启动/TERM 均优雅退出，重启不等租约且探针 lag=0。"""
    snapshot = await _snapshot_and_align_offsets(sessionmaker_pg)
    try:
        first_log = await _start_and_term_worker()
        second_log = await _start_and_term_worker()
        assert 'IM 消费者进程启动' in first_log
        assert 'IM 消费者进程已优雅停止' in first_log
        assert 'IM 消费者进程启动' in second_log
        assert 'IM 消费者进程已优雅停止' in second_log

        probe = await collect_probe(
            sessionmaker_pg,
            producer_session_factory=sessionmaker_pg,
        )
        assert all(item.lag == 0 for item in probe.consumers)
        assert all(item.lease_owner is None for item in probe.consumers)
        assert {item.producer for item in probe.producer_outboxes} == {
            'relation',
            'notification',
            'community',
            'session',
            'task',
            'group',
        }
    finally:
        await _restore_offsets(sessionmaker_pg, snapshot)


@pytest.mark.asyncio
async def test_probe_cli_outputs_machine_readable_json(sessionmaker_pg) -> None:
    """探针 CLI 返回 JSON，字段覆盖 head/cursor/lag/failure/DLQ。"""
    snapshot = await _snapshot_and_align_offsets(sessionmaker_pg)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            '-m',
            'backend.app.hasn_im.consumer_worker',
            'probe',
            '--max-lag',
            '0',
            cwd=Path(__file__).resolve().parents[4],
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        payload = json.loads(stdout)
        assert payload['lag_ok'] is True
        expected_returncode = 0 if payload['healthy'] else 1
        assert process.returncode == expected_returncode, stderr.decode()
        assert {item['consumer'] for item in payload['consumers']} == set(
            _CONSUMERS
        )
    finally:
        await _restore_offsets(sessionmaker_pg, snapshot)


def test_supervisor_manages_only_huanxing_im_consumer() -> None:
    """Supervisor 配置使用独立 program、TERM 与足够的事务收尾时间。"""
    config = (
        Path(__file__).resolve().parents[4]
        / 'deploy/backend/supervisor/fba_hasn_im_consumer.conf'
    ).read_text(encoding='utf-8')
    assert '[program:fba_hasn_im_consumer]' in config
    assert 'backend.app.hasn_im.consumer_worker run' in config
    assert 'stopsignal=TERM' in config
    assert 'stopwaitsecs=120' in config
