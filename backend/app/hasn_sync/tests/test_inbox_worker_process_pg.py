"""独立 sync inbox worker 进程、探针与 Supervisor 的真实 PostgreSQL 回归。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.sync_business_handlers import (
    build_sync_handler_registry,
)
from backend.app.hasn.sync_inbox_worker import collect_probe
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES


_INBOX = SCHEMA_NAMES.sync_table('hasn_sync_inbox_events')


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text('SELECT 1'))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 sync worker 进程测试：{exc!r}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _assert_no_unfinished_rows(sessionmaker) -> None:
    """进程生命周期测试不得误消费其它用例尚未完成的 inbox。"""
    event_types = list(build_sync_handler_registry())
    async with sessionmaker() as db:
        count = int(
            (
                await db.execute(
                        sa.text(
                            f'SELECT count(*) FROM {_INBOX} '  # noqa: S608 内部常量表名
                            "WHERE status IN ('accepted', 'processing', 'retry')"
                            'AND event_type = ANY(CAST(:event_types AS text[]))'
                        ),
                        {'event_types': event_types},
                )
            ).scalar_one()
        )
    assert count == 0, f'sync inbox 存在 {count} 条未完成行，不能安全执行进程生命周期测试'


async def _start_and_term_worker() -> str:
    """启动真实子进程，看到就绪日志后发送 TERM。"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'backend.app.hasn.sync_inbox_worker',
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
            raise AssertionError('sync worker 在安装信号处理器前异常退出')
        captured.append(line)
        if 'sync inbox worker 进程启动' in line.decode('utf-8', errors='replace'):
            break
    process.terminate()
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0
    return b''.join([*captured, stdout]).decode('utf-8', errors='replace')


@pytest.mark.asyncio
async def test_real_process_term_and_probe(sessionmaker_pg) -> None:
    """真实进程能优雅 TERM，探针返回受限数据库角色与机器可读计数。"""
    await _assert_no_unfinished_rows(sessionmaker_pg)
    log = await _start_and_term_worker()
    assert 'sync inbox worker 进程启动' in log
    assert 'sync inbox worker 进程已优雅停止' in log

    probe = await collect_probe(sessionmaker_pg)
    assert probe.healthy is True
    assert probe.pending == 0
    assert probe.processing == 0
    assert probe.retry == 0
    assert probe.database_role


@pytest.mark.asyncio
async def test_probe_cli_outputs_machine_readable_json(sessionmaker_pg) -> None:
    """探针 CLI 返回 JSON，并在无未决行时以零退出。"""
    await _assert_no_unfinished_rows(sessionmaker_pg)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'backend.app.hasn.sync_inbox_worker',
        'probe',
        cwd=Path(__file__).resolve().parents[4],
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0, stderr.decode()
    payload = json.loads(stdout)
    assert payload['healthy'] is True
    assert payload['pending'] == 0
    assert payload['processing'] == 0
    assert payload['retry'] == 0


def test_supervisor_manages_dedicated_sync_worker() -> None:
    """Supervisor 必须单独管理 sync worker，并给当前事务足够收尾时间。"""
    config = (
        Path(__file__).resolve().parents[4]
        / 'deploy/backend/supervisor/fba_hasn_sync_worker.conf'
    ).read_text(encoding='utf-8')
    assert '[program:fba_hasn_sync_worker]' in config
    assert 'backend.app.hasn.sync_inbox_worker run' in config
    assert 'stopsignal=TERM' in config
    assert 'stopwaitsecs=120' in config
