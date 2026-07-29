from __future__ import annotations

import base64
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from celery import Celery
from celery.exceptions import TaskRevokedError
from celery.result import allow_join_result
from kombu import Exchange, Queue
from kombu.exceptions import OperationalError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from backend.app.task.celery import (
    CELERY_DEFAULT_EXCHANGE,
    CELERY_DEFAULT_QUEUE,
    build_celery_broker_options,
    init_celery,
)
from backend.app.task.model.scheduler import TaskScheduler
from backend.core.conf import settings
from backend.tests.tasks.rabbitmq_fault_proxy import (
    RabbitMQFaultMode,
    RabbitMQFaultProxy,
)

PRODUCTION_PROBE_TASK_NAME = 'credit_outbox_metrics_refresh'
RETRY_TASK_NAME = 'celery_rabbitmq_e2e.retry'
COUNTDOWN_TASK_NAME = 'celery_rabbitmq_e2e.countdown'
IDEMPOTENT_TASK_NAME = 'celery_rabbitmq_e2e.idempotent'
ACK_INTERRUPTION_TASK_NAME = 'celery_rabbitmq_e2e.ack_interruption'
FLOWER_E2E_BASIC_AUTH = 'flower-e2e:V7rP4mZ2nQ8sK6xD9cT5wL3j'

pytestmark = pytest.mark.skipif(
    os.getenv('CELERY_RABBITMQ_E2E') != '1',
    reason='仅在显式提供真实 RabbitMQ 与 PostgreSQL 环境时运行',
)


@dataclass(frozen=True)
class RabbitMQE2EContext:
    app: Celery
    queue: Queue
    exchange: Exchange
    suffix: str
    idempotency_table: str
    database_engine: Engine


@dataclass
class RabbitMQWorker:
    process: subprocess.Popen[bytes]
    hostname: str
    log_file: tempfile._TemporaryFileWrapper[bytes]

    def log_tail(self) -> str:
        """返回 worker 日志尾部，用于报告真实启动或退出故障。"""
        self.log_file.flush()
        log_text = (
            Path(self.log_file.name)
            .read_bytes()
            .decode(
                'utf-8',
                errors='replace',
            )
        )
        return log_text[-8000:]


def _database_url(app: Celery) -> str:
    """返回移除 Celery backend 前缀后的 SQLAlchemy URL。"""
    backend_url = str(app.conf.result_backend)
    if not backend_url.startswith('db+'):
        raise RuntimeError('真实 RabbitMQ E2E 必须使用数据库 result backend')
    return backend_url.removeprefix('db+')


def _safe_test_table_name(suffix: str) -> str:
    """构造仅包含可信字符的临时测试表名。"""
    table_name = f'celery_e2e_{suffix}'
    if not table_name.replace('_', '').isalnum():
        raise ValueError('Celery E2E 临时表名不合法')
    return table_name


@contextmanager
def _temporary_beat_database(app: Celery) -> Iterator[tuple[str, Engine]]:
    """创建仅含调度表的临时 PostgreSQL 数据库，隔离真实 DatabaseScheduler。"""
    source_url = make_url(_database_url(app))
    database_name = f'celery_beat_e2e_{uuid.uuid4().hex}'
    if not database_name.replace('_', '').isalnum():
        raise ValueError('Beat E2E 临时数据库名不合法')
    admin_engine = create_engine(source_url, isolation_level='AUTOCOMMIT')
    temporary_url = source_url.set(database=database_name)
    temporary_engine: Engine | None = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        temporary_engine = create_engine(temporary_url)
        cast('Any', TaskScheduler.__table__).create(temporary_engine)
        with temporary_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO task_scheduler (
                        name, task, type, interval_every, interval_period,
                        one_off, enabled, total_run_count, created_time
                    )
                    VALUES (
                        'legacy-demo-task', 'task_demo', 0, 30, 'seconds',
                        false, true, 0, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        yield database_name, temporary_engine
    finally:
        if temporary_engine is not None:
            temporary_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()


@contextmanager
def _isolated_rabbitmq_context(label: str) -> Iterator[RabbitMQE2EContext]:
    """建立真实隔离拓扑和 PostgreSQL 表，并在结束后确定性删除。"""
    assert settings.CELERY_BROKER == 'rabbitmq'
    suffix = uuid.uuid4().hex
    exchange = Exchange(
        f'{CELERY_DEFAULT_EXCHANGE}.e2e.{label}.{suffix}',
        type='direct',
        durable=True,
        auto_delete=False,
    )
    queue = Queue(
        f'{CELERY_DEFAULT_QUEUE}.e2e.{label}.{suffix}',
        exchange=exchange,
        routing_key=f'{CELERY_DEFAULT_QUEUE}.e2e.{label}.{suffix}',
        durable=True,
        auto_delete=False,
        queue_arguments={'x-queue-type': 'classic'},
    )

    app = init_celery()
    options = build_celery_broker_options(settings)
    options.update(
        task_default_queue=queue.name,
        task_default_exchange=exchange.name,
        task_default_routing_key=queue.routing_key,
        task_queues=(queue,),
    )
    app.conf.update(options)
    app.conf.task_always_eager = False

    table_name = _safe_test_table_name(suffix)
    database_engine = create_engine(_database_url(app))
    with database_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE "{table_name}" (
                    marker TEXT PRIMARY KEY,
                    deliveries INTEGER NOT NULL,
                    applied INTEGER NOT NULL
                )
                """
            )
        )
    with app.connection_for_write() as connection:
        channel = connection.channel()
        exchange(channel).declare()
        queue(channel).declare()

    context = RabbitMQE2EContext(
        app=app,
        queue=queue,
        exchange=exchange,
        suffix=suffix,
        idempotency_table=table_name,
        database_engine=database_engine,
    )
    try:
        yield context
    finally:
        original_error = sys.exc_info()[0]
        cleanup_error: BaseException | None = None
        try:
            with app.connection_for_write() as connection:
                channel = connection.channel()
                queue(channel).delete(if_unused=False, if_empty=False)
                exchange(channel).delete(if_unused=False)
        except BaseException as exc:
            cleanup_error = exc
        try:
            with database_engine.begin() as connection:
                connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        finally:
            app.close()
            database_engine.dispose()
        if cleanup_error is not None and original_error is None:
            raise cleanup_error


@pytest.fixture(scope='module')
def rabbitmq_e2e_context() -> Iterator[RabbitMQE2EContext]:
    with _isolated_rabbitmq_context('standard') as context:
        yield context


@pytest.fixture
def rabbitmq_fault_context() -> Iterator[RabbitMQE2EContext]:
    with _isolated_rabbitmq_context('fault') as context:
        yield context


def _worker_environment(context: RabbitMQE2EContext) -> dict[str, str]:
    """构造不打印凭据的真实 worker 环境。"""
    environment = os.environ.copy()
    environment.update(
        CELERY_RABBITMQ_E2E_SUFFIX=context.suffix,
        CELERY_RABBITMQ_E2E_QUEUE=context.queue.name,
        CELERY_RABBITMQ_E2E_EXCHANGE=context.exchange.name,
        CELERY_RABBITMQ_E2E_IDEMPOTENCY_TABLE=context.idempotency_table,
        CELERY_BROKER_URL=str(context.app.conf.broker_url),
        CELERY_RESULT_BACKEND=str(context.app.conf.result_backend),
        BROKER_URL=str(context.app.conf.broker_url),
        RESULT_BACKEND=str(context.app.conf.result_backend),
    )
    return environment


def _start_worker(
    context: RabbitMQE2EContext,
    *,
    pool: str = 'solo',
) -> RabbitMQWorker:
    """启动只消费隔离 queue 的独立 Celery worker。"""
    hostname = f'celery-e2e-{uuid.uuid4().hex[:12]}@localhost'
    # 文件生命周期跨越本函数，并由 _stop_worker 在进程结束后统一关闭。
    log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix='celery-rabbitmq-e2e-',
        suffix='.log',
    )
    process = subprocess.Popen(
        [
            sys.executable,
            '-m',
            'celery',
            '-A',
            'backend.tests.tasks.celery_rabbitmq_e2e_worker:app',
            'worker',
            f'--pool={pool}',
            '--concurrency=1',
            f'--hostname={hostname}',
            f'--queues={context.queue.name}',
            '--loglevel=INFO',
            '--without-gossip',
            '--without-mingle',
        ],
        cwd=Path(__file__).parents[3],
        env=_worker_environment(context),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    worker = RabbitMQWorker(
        process=process,
        hostname=hostname,
        log_file=log_file,
    )
    try:
        _wait_for_worker(context.app, worker)
    except BaseException:
        _stop_worker(context.app, worker)
        raise
    return worker


def _wait_for_worker(
    app: Celery,
    worker: RabbitMQWorker,
    *,
    timeout: float = 40,
) -> None:
    """等待独立 worker 通过真实 remote-control ping。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = worker.process.poll()
        if return_code is not None:
            raise RuntimeError(f'Celery E2E worker 提前退出，退出码 {return_code}：\n{worker.log_tail()}')
        replies = app.control.ping(
            destination=[worker.hostname],
            timeout=1,
        )
        if replies == [{worker.hostname: {'ok': 'pong'}}]:
            return
        time.sleep(0.5)
    raise TimeoutError(f'Celery E2E worker 未在期限内就绪：\n{worker.log_tail()}')


def _stop_worker(app: Celery, worker: RabbitMQWorker) -> None:
    """停止 worker；remote shutdown 失败时终止整个测试进程组。"""
    process = worker.process
    try:
        if process.poll() is None:
            with contextlib.suppress(Exception):
                app.control.shutdown(destination=[worker.hostname])
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
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        worker.log_file.close()


def _kill_worker_process_group(worker: RabbitMQWorker) -> None:
    """模拟 consumer ACK 前整组 worker 硬退出。"""
    if worker.process.poll() is None:
        os.killpg(worker.process.pid, signal.SIGKILL)
        worker.process.wait(timeout=10)


@pytest.fixture(scope='module')
def rabbitmq_worker(
    rabbitmq_e2e_context: RabbitMQE2EContext,
) -> Iterator[RabbitMQWorker]:
    worker = _start_worker(rabbitmq_e2e_context)
    try:
        yield worker
    finally:
        _stop_worker(rabbitmq_e2e_context.app, worker)


def _queue_depth(context: RabbitMQE2EContext) -> int:
    """读取真实 RabbitMQ queue 的 ready 消息数。"""
    with context.app.connection_for_read() as connection:
        channel = cast('Any', connection.default_channel)
        declared = channel.queue_declare(
            queue=context.queue.name,
            passive=True,
        )
    return int(declared[1])


def _fault_broker_url(broker_url: str, proxy_port: int) -> str:
    """保留已编码凭据和 vhost，只把目标替换为本地故障代理。"""
    parsed = urllib.parse.urlsplit(broker_url)
    user_info, separator, _endpoint = parsed.netloc.rpartition('@')
    if not separator:
        raise ValueError('RabbitMQ E2E broker URL 缺少认证信息')
    return urllib.parse.urlunsplit((
        parsed.scheme,
        f'{user_info}@127.0.0.1:{proxy_port}',
        parsed.path,
        parsed.query,
        parsed.fragment,
    ))


def _fault_publisher_app(
    context: RabbitMQE2EContext,
    *,
    proxy_port: int,
) -> Celery:
    """构造只连故障代理、不允许静默重试或切到 Redis 的 producer。"""
    app = Celery(
        f'celery-rabbitmq-fault-{context.suffix}',
        broker=_fault_broker_url(str(context.app.conf.broker_url), proxy_port),
        backend=str(context.app.conf.result_backend),
    )
    options = build_celery_broker_options(settings)
    options.update(
        task_default_queue=context.queue.name,
        task_default_exchange=context.exchange.name,
        task_default_routing_key=context.queue.routing_key,
        task_queues=(context.queue,),
        task_publish_retry=False,
    )
    app.conf.update(options)
    return app


def _idempotency_counts(
    context: RabbitMQE2EContext,
    marker: str,
) -> tuple[int, int] | None:
    """读取真实 PostgreSQL 中的投递次数与业务应用次数。"""
    with context.database_engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT deliveries, applied
                FROM "{context.idempotency_table}"
                WHERE marker = :marker
                """
            ),
            {'marker': marker},
        ).one_or_none()
    if row is None:
        return None
    return int(row.deliveries), int(row.applied)


def _wait_for_idempotency_counts(
    context: RabbitMQE2EContext,
    marker: str,
    *,
    minimum_deliveries: int,
    timeout: float = 30,
) -> tuple[int, int]:
    """等待 worker 把预期投递写入真实 PostgreSQL。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        counts = _idempotency_counts(context, marker)
        if counts is not None and counts[0] >= minimum_deliveries:
            return counts
        time.sleep(0.25)
    raise TimeoutError('Celery E2E 幂等投递未在期限内完成')


def _wait_until_task_scheduled(
    app: Celery,
    worker: RabbitMQWorker,
    task_id: str,
    *,
    timeout: float = 20,
) -> None:
    """等待 countdown 任务进入指定 worker 的 scheduled 集合。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduled = app.control.inspect(
            destination=[worker.hostname],
            timeout=2,
        ).scheduled()
        if scheduled and any(
            item.get('request', {}).get('id') == task_id for item in scheduled.get(worker.hostname, [])
        ):
            return
        time.sleep(0.25)
    raise TimeoutError('待撤销任务未进入 scheduled 集合')


def _terminate_subprocess(process: subprocess.Popen[bytes]) -> None:
    """终止 Beat 或 Flower 测试进程组。"""
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_real_rabbitmq_runs_task_and_persists_postgresql_result(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    result = rabbitmq_e2e_context.app.send_task(
        PRODUCTION_PROBE_TASK_NAME,
        queue=rabbitmq_e2e_context.queue.name,
    )
    try:
        with allow_join_result():
            value = result.get(timeout=20)
        assert value.startswith('履约指标已刷新:')
        assert result.backend.__class__.__name__ == 'DatabaseBackend'
        assert result.backend.as_uri().startswith('postgresql+psycopg')
    finally:
        result.forget()


def test_real_rabbitmq_failure_retry_executes_exactly_twice(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    marker = f'retry-{uuid.uuid4().hex}'
    result = rabbitmq_e2e_context.app.send_task(
        RETRY_TASK_NAME,
        args=(marker,),
        queue=rabbitmq_e2e_context.queue.name,
    )
    try:
        with allow_join_result():
            assert result.get(timeout=20) == {'value': marker, 'retries': 1}
    finally:
        result.forget()


def test_real_rabbitmq_honors_countdown(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    published_at = time.time()
    result = rabbitmq_e2e_context.app.send_task(
        COUNTDOWN_TASK_NAME,
        countdown=2,
        queue=rabbitmq_e2e_context.queue.name,
    )
    try:
        with allow_join_result():
            executed_at = result.get(timeout=20)
        assert executed_at - published_at >= 1.5
    finally:
        result.forget()


def test_real_rabbitmq_remote_control_supports_inspect(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    app = rabbitmq_e2e_context.app
    hostname = rabbitmq_worker.hostname
    inspector = app.control.inspect(destination=[hostname], timeout=5)

    ping = inspector.ping()
    registered = inspector.registered()

    assert ping == {hostname: {'ok': 'pong'}}
    assert registered is not None
    assert hostname in registered


def test_real_rabbitmq_revoke_cancels_scheduled_task(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    app = rabbitmq_e2e_context.app
    result = app.send_task(
        PRODUCTION_PROBE_TASK_NAME,
        countdown=30,
        queue=rabbitmq_e2e_context.queue.name,
    )
    try:
        _wait_until_task_scheduled(app, rabbitmq_worker, result.id)
        app.control.revoke(
            result.id,
            destination=[rabbitmq_worker.hostname],
        )
        with allow_join_result(), pytest.raises(TaskRevokedError):
            result.get(timeout=20)
    finally:
        result.forget()


def test_real_rabbitmq_flower_style_event_receiver_captures_heartbeat(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    app = rabbitmq_e2e_context.app
    captured: list[dict[str, object]] = []
    with app.connection() as connection:
        receiver = app.events.Receiver(
            connection,
            handlers={'*': captured.append},
            node_id=f'celery-e2e-events-{uuid.uuid4().hex}',
        )
        receiver.capture(limit=1, timeout=10, wakeup=True)

    assert captured
    assert captured[0].get('hostname') == rabbitmq_worker.hostname


def test_real_rabbitmq_beat_publishes_periodic_probe(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    marker = f'beat-{uuid.uuid4().hex}'
    environment = _worker_environment(rabbitmq_e2e_context)
    environment['CELERY_RABBITMQ_E2E_BEAT_MARKER'] = marker
    environment['CELERY_REDIS_PREFIX'] = f'celery:e2e:beat:{uuid.uuid4().hex}'
    # 文件生命周期覆盖独立 Beat 进程，并在 finally 中关闭。
    log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix='celery-rabbitmq-e2e-beat-',
        suffix='.log',
    )
    with _temporary_beat_database(rabbitmq_e2e_context.app) as (
        database_name,
        beat_database_engine,
    ):
        environment['DATABASE_SCHEMA'] = database_name
        process = subprocess.Popen(
            [
                sys.executable,
                '-m',
                'celery',
                '-A',
                'backend.tests.tasks.celery_rabbitmq_e2e_beat:app',
                'beat',
                '--loglevel=DEBUG',
                '--scheduler=backend.app.task.utils.schedulers:DatabaseScheduler',
            ],
            cwd=Path(__file__).parents[3],
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            counts = _wait_for_idempotency_counts(
                rabbitmq_e2e_context,
                marker,
                minimum_deliveries=1,
                timeout=30,
            )
            assert counts[1] == 1
            # Beat 正常退出时 scheduler.close() 会把 reserve 后的运行次数同步回数据库。
            _terminate_subprocess(process)
            with beat_database_engine.connect() as connection:
                total_run_count = connection.execute(
                    text('SELECT total_run_count FROM task_scheduler WHERE name = :name'),
                    {'name': 'rabbitmq-e2e-beat-probe'},
                ).scalar_one()
                retired_demo_enabled = connection.execute(
                    text('SELECT enabled FROM task_scheduler WHERE name = :name'),
                    {'name': 'legacy-demo-task'},
                ).scalar_one()
            assert int(total_run_count) >= 1
            assert retired_demo_enabled is False
        except BaseException as exc:
            log_file.flush()
            log_file.seek(0)
            log_tail = log_file.read().decode('utf-8', errors='replace')[-8000:]
            with beat_database_engine.connect() as connection:
                scheduler_state = (
                    connection
                    .execute(
                        text(
                            'SELECT name, enabled, interval_every, interval_period, '
                            'total_run_count, last_run_time FROM task_scheduler ORDER BY name'
                        )
                    )
                    .mappings()
                    .all()
                )
            raise RuntimeError(
                f'真实 Beat E2E 失败，process_exit={process.poll()}，scheduler_state={scheduler_state!r}：\n{log_tail}'
            ) from exc
        finally:
            _terminate_subprocess(process)
            log_file.close()


def test_actual_flower_process_authenticates_and_lists_worker(
    rabbitmq_e2e_context: RabbitMQE2EContext,
    rabbitmq_worker: RabbitMQWorker,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(('127.0.0.1', 0))
        port = int(probe_socket.getsockname()[1])
    environment = _worker_environment(rabbitmq_e2e_context)
    environment['FLOWER_BASIC_AUTH'] = FLOWER_E2E_BASIC_AUTH
    # 文件生命周期覆盖独立 Flower 进程，并在 finally 中关闭。
    log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix='celery-rabbitmq-e2e-flower-',
        suffix='.log',
    )
    process = subprocess.Popen(
        [
            sys.executable,
            '-m',
            'backend.app.task.flower',
            f'--port={port}',
        ],
        cwd=Path(__file__).parents[3],
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    username, password = FLOWER_E2E_BASIC_AUTH.split(':', maxsplit=1)
    authorization = base64.b64encode(
        f'{username}:{password}'.encode(),
    ).decode()
    status_request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/workers?status=true',
        headers={'Authorization': f'Basic {authorization}'},
    )
    worker_query = urllib.parse.urlencode({
        'refresh': 'true',
        'workername': rabbitmq_worker.hostname,
    })
    detail_request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/workers?{worker_query}',
        headers={'Authorization': f'Basic {authorization}'},
    )
    try:
        deadline = time.monotonic() + 40
        last_error: BaseException | None = None
        worker_status: dict[str, bool] = {}
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                # URL 由本测试构造且协议固定为 http、主机固定为回环地址。
                with urllib.request.urlopen(  # noqa: S310
                    status_request,
                    timeout=5,
                ) as status_response:
                    worker_status = json.load(status_response)
                if worker_status.get(rabbitmq_worker.hostname) is True:
                    break
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(0.5)
        else:
            worker_status = {}

        if worker_status.get(rabbitmq_worker.hostname) is not True:
            raise TimeoutError(f'Flower 事件状态未在期限内发现测试 worker，最近错误类型 {type(last_error).__name__}')

        unauthenticated_request = urllib.request.Request(f'http://127.0.0.1:{port}/api/workers?status=true')
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            # URL 由本测试构造且协议固定为 http、主机固定为回环地址。
            urllib.request.urlopen(unauthenticated_request, timeout=5)  # noqa: S310
        assert unauthorized.value.code == 401

        # URL 由本测试构造且协议固定为 http、主机固定为回环地址。
        with urllib.request.urlopen(  # noqa: S310
            detail_request,
            timeout=20,
        ) as detail_response:
            workers = json.load(detail_response)
        assert detail_response.status == 200
        assert rabbitmq_worker.hostname in workers
    except BaseException as exc:
        log_file.flush()
        log_file.seek(0)
        log_tail = log_file.read().decode('utf-8', errors='replace')[-8000:]
        raise RuntimeError(f'真实 Flower E2E 失败：\n{log_tail}') from exc
    finally:
        _terminate_subprocess(process)
        log_file.close()


def test_publish_interruption_before_basic_publish_never_enqueues(
    rabbitmq_fault_context: RabbitMQE2EContext,
) -> None:
    with RabbitMQFaultProxy(
        upstream_host=settings.CELERY_RABBITMQ_HOST,
        upstream_port=settings.CELERY_RABBITMQ_PORT,
        mode=RabbitMQFaultMode.BEFORE_PUBLISH,
    ) as proxy:
        publisher = _fault_publisher_app(
            rabbitmq_fault_context,
            proxy_port=proxy.port,
        )
        try:
            with pytest.raises(OperationalError):
                publisher.send_task(
                    PRODUCTION_PROBE_TASK_NAME,
                    queue=rabbitmq_fault_context.queue.name,
                    retry=False,
                )
            proxy.wait_for_fault()
        finally:
            publisher.close()

    assert _queue_depth(rabbitmq_fault_context) == 0


def test_confirm_interruption_retries_with_one_idempotent_effect(
    rabbitmq_fault_context: RabbitMQE2EContext,
) -> None:
    marker = f'before-confirm-{uuid.uuid4().hex}'
    task_id = uuid.uuid4().hex
    with RabbitMQFaultProxy(
        upstream_host=settings.CELERY_RABBITMQ_HOST,
        upstream_port=settings.CELERY_RABBITMQ_PORT,
        mode=RabbitMQFaultMode.BEFORE_CONFIRM,
    ) as proxy:
        publisher = _fault_publisher_app(
            rabbitmq_fault_context,
            proxy_port=proxy.port,
        )
        try:
            with pytest.raises(OperationalError):
                publisher.send_task(
                    IDEMPOTENT_TASK_NAME,
                    args=(marker,),
                    task_id=task_id,
                    queue=rabbitmq_fault_context.queue.name,
                    retry=False,
                )
            proxy.wait_for_fault()
        finally:
            publisher.close()

    assert _queue_depth(rabbitmq_fault_context) == 1
    retry_result = rabbitmq_fault_context.app.send_task(
        IDEMPOTENT_TASK_NAME,
        args=(marker,),
        task_id=task_id,
        queue=rabbitmq_fault_context.queue.name,
    )
    assert _queue_depth(rabbitmq_fault_context) == 2

    worker = _start_worker(rabbitmq_fault_context)
    try:
        deliveries, applied = _wait_for_idempotency_counts(
            rabbitmq_fault_context,
            marker,
            minimum_deliveries=2,
        )
        assert deliveries == 2
        assert applied == 1
    finally:
        _stop_worker(rabbitmq_fault_context.app, worker)
        retry_result.forget()


def test_consumer_ack_interruption_requeues_delivery(
    rabbitmq_fault_context: RabbitMQE2EContext,
) -> None:
    app = rabbitmq_fault_context.app
    marker = f'before-ack-{uuid.uuid4().hex}'
    first_worker = _start_worker(rabbitmq_fault_context)
    result = app.send_task(
        ACK_INTERRUPTION_TASK_NAME,
        args=(marker,),
        queue=rabbitmq_fault_context.queue.name,
    )
    second_worker: RabbitMQWorker | None = None
    try:
        try:
            counts = _wait_for_idempotency_counts(
                rabbitmq_fault_context,
                marker,
                minimum_deliveries=1,
            )
        except BaseException as exc:
            raise RuntimeError(f'ACK 故障首个 worker 未完成落库：\n{first_worker.log_tail()}') from exc
        assert counts == (1, 1)
        _kill_worker_process_group(first_worker)
        first_worker.log_file.close()

        second_worker = _start_worker(rabbitmq_fault_context)
        with allow_join_result():
            payload = result.get(timeout=30)
        assert payload == {
            'marker': marker,
            'redelivered': True,
            'deliveries': 2,
            'applied': 1,
        }
        assert _idempotency_counts(rabbitmq_fault_context, marker) == (2, 1)
    finally:
        if first_worker.process.poll() is None:
            _stop_worker(app, first_worker)
        if second_worker is not None:
            _stop_worker(app, second_worker)
        result.forget()
