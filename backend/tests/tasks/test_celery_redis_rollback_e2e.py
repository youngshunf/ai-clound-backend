"""Celery Redis broker 回滚路径的真实服务验收。"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from collections.abc import Iterator
from pathlib import Path

import pytest

from celery import Celery
from celery.result import allow_join_result
from redis import Redis

from backend.app.task.celery import CELERY_REDIS_ROLLBACK_QUEUE, build_celery_broker_url, init_celery
from backend.core.conf import settings

pytestmark = pytest.mark.skipif(
    os.getenv('CELERY_REDIS_E2E') != '1',
    reason='仅在显式提供隔离 Redis DB 与真实 PostgreSQL 时运行',
)

TASK_NAME = 'credit_outbox_metrics_refresh'


def _wait_for_worker(app: Celery, process: subprocess.Popen[bytes], hostname: str, log_path: Path) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            log_tail = log_path.read_text(encoding='utf-8', errors='replace')[-8000:]
            raise RuntimeError(f'Redis rollback worker 提前退出，退出码 {return_code}：\n{log_tail}')
        if app.control.ping(destination=[hostname], timeout=1) == [{hostname: {'ok': 'pong'}}]:
            return
        time.sleep(0.5)
    log_tail = log_path.read_text(encoding='utf-8', errors='replace')[-8000:]
    raise TimeoutError(f'Redis rollback worker 未在期限内就绪：\n{log_tail}')


def _stop_worker(app: Celery, process: subprocess.Popen[bytes], hostname: str) -> None:
    if process.poll() is None:
        with contextlib.suppress(Exception):
            app.control.shutdown(destination=[hostname])
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


@pytest.fixture(scope='module')
def redis_rollback_app() -> Iterator[tuple[Celery, Redis]]:
    assert settings.CELERY_BROKER == 'redis'
    assert settings.CELERY_BROKER_REDIS_DATABASE == 15
    app = init_celery()
    client = Redis.from_url(build_celery_broker_url(settings), decode_responses=True)
    assert client.ping() is True
    initial_keys = set(client.scan_iter(match='*'))
    assert not initial_keys, f'隔离 Redis DB 15 含 {len(initial_keys)} 个既有键，拒绝运行回滚 E2E'
    try:
        yield app, client
    finally:
        created_keys = list(client.scan_iter(match='*'))
        if created_keys:
            client.delete(*created_keys)
        client.close()
        app.close()


def test_real_redis_rollback_consumes_production_task_and_persists_result(
    redis_rollback_app: tuple[Celery, Redis],
) -> None:
    app, client = redis_rollback_app
    assert app.conf.task_default_queue == CELERY_REDIS_ROLLBACK_QUEUE == 'celery'
    assert app.conf.broker_transport_options == {}
    worker_environment = os.environ.copy()
    # `CELERY_BROKER` 是本项目的模式开关，但 Celery CLI 会把同名环境变量误当 URL；
    # 子进程已通过完整 URL 明确指定 transport，因此移除有歧义的短值。
    worker_environment.pop('CELERY_BROKER', None)
    worker_environment['CELERY_BROKER_URL'] = build_celery_broker_url(settings)

    hostname = f'celery-redis-rollback-{uuid.uuid4().hex[:12]}@localhost'
    with tempfile.NamedTemporaryFile(prefix='celery-redis-rollback-', suffix='.log') as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                '-m',
                'celery',
                '-A',
                'backend.app.task.celery:celery_app',
                'worker',
                '--pool=solo',
                '--concurrency=1',
                f'--hostname={hostname}',
                f'--queues={CELERY_REDIS_ROLLBACK_QUEUE}',
                '--loglevel=INFO',
                '--without-gossip',
                '--without-mingle',
            ],
            cwd=Path(__file__).parents[3],
            env=worker_environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_for_worker(app, process, hostname, Path(log_file.name))
            registered = app.control.inspect(destination=[hostname], timeout=5).registered()
            assert registered is not None
            assert TASK_NAME in registered[hostname]

            result = app.send_task(TASK_NAME, queue=CELERY_REDIS_ROLLBACK_QUEUE)
            try:
                with allow_join_result():
                    value = result.get(timeout=30)
                assert value.startswith('履约指标已刷新:')
                assert result.backend.__class__.__name__ == 'DatabaseBackend'
                assert result.backend.as_uri().startswith('postgresql+psycopg')
            finally:
                result.forget()

            inspector = app.control.inspect(destination=[hostname], timeout=5)
            assert inspector.active() == {hostname: []}
            assert inspector.reserved() == {hostname: []}
            assert inspector.scheduled() == {hostname: []}
            assert client.llen(CELERY_REDIS_ROLLBACK_QUEUE) == 0
        finally:
            _stop_worker(app, process, hostname)
