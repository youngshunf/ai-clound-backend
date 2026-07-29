from __future__ import annotations

import os
import time

from celery import Task
from kombu import Exchange, Queue
from sqlalchemy import create_engine, text

from backend.app.task.celery import build_celery_broker_options, init_celery
from backend.core.conf import settings

RETRY_TASK_NAME = 'celery_rabbitmq_e2e.retry'
COUNTDOWN_TASK_NAME = 'celery_rabbitmq_e2e.countdown'
IDEMPOTENT_TASK_NAME = 'celery_rabbitmq_e2e.idempotent'
ACK_INTERRUPTION_TASK_NAME = 'celery_rabbitmq_e2e.ack_interruption'
BEAT_PROBE_TASK_NAME = 'celery_rabbitmq_e2e.beat_probe'


def _required_environment(name: str) -> str:
    """读取真实 E2E worker 必需的环境变量。"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'缺少真实 RabbitMQ E2E 环境变量：{name}')
    return value


suffix = _required_environment('CELERY_RABBITMQ_E2E_SUFFIX')
queue_name = _required_environment('CELERY_RABBITMQ_E2E_QUEUE')
exchange_name = _required_environment('CELERY_RABBITMQ_E2E_EXCHANGE')
idempotency_table = _required_environment('CELERY_RABBITMQ_E2E_IDEMPOTENCY_TABLE')
result_backend_url = _required_environment('CELERY_RESULT_BACKEND')
if not result_backend_url.startswith('db+'):
    raise RuntimeError('真实 RabbitMQ E2E 必须使用数据库 result backend')
database_engine = create_engine(result_backend_url.removeprefix('db+'))

exchange = Exchange(
    exchange_name,
    type='direct',
    durable=True,
    auto_delete=False,
)
queue = Queue(
    queue_name,
    exchange=exchange,
    routing_key=queue_name,
    durable=True,
    auto_delete=False,
    queue_arguments={'x-queue-type': 'classic'},
)

app = init_celery()
options = build_celery_broker_options(settings)
options.update(
    task_default_queue=queue_name,
    task_default_exchange=exchange_name,
    task_default_routing_key=queue_name,
    task_queues=(queue,),
)
app.conf.update(options)


@app.task(name=RETRY_TASK_NAME, bind=True, base=Task, max_retries=1)
def retry_task(task: Task, value: str) -> dict[str, object]:
    """首次失败后走 Celery retry，第二次返回真实重试次数。"""
    if task.request.retries == 0:
        raise task.retry(exc=RuntimeError('预期中的首次失败'), countdown=0)
    return {'value': value, 'retries': task.request.retries}


@app.task(name=COUNTDOWN_TASK_NAME, base=Task)
def countdown_task() -> float:
    """返回真实执行时刻，用于验证 broker ETA/countdown。"""
    return time.time()


def _record_idempotent_delivery(marker: str) -> dict[str, int]:
    """在真实 PostgreSQL 中记录投递次数，并只应用一次业务效果。"""
    statement = text(
        f"""
        INSERT INTO "{idempotency_table}" (marker, deliveries, applied)
        VALUES (:marker, 1, 1)
        ON CONFLICT (marker) DO UPDATE
        SET deliveries = "{idempotency_table}".deliveries + 1
        RETURNING deliveries, applied
        """
    )
    with database_engine.begin() as connection:
        row = connection.execute(statement, {'marker': marker}).one()
    return {'deliveries': int(row.deliveries), 'applied': int(row.applied)}


@app.task(name=IDEMPOTENT_TASK_NAME, base=Task)
def idempotent_task(marker: str) -> dict[str, int]:
    """重复投递可执行多次，但真实业务效果只落一次。"""
    return _record_idempotent_delivery(marker)


@app.task(
    name=ACK_INTERRUPTION_TASK_NAME,
    bind=True,
    base=Task,
    acks_late=True,
    reject_on_worker_lost=True,
)
def ack_interruption_task(task: Task, marker: str) -> dict[str, object]:
    """首次副作用落库后停在 ACK 前，重投时保持业务效果幂等。"""
    delivery_info = task.request.delivery_info or {}
    redelivered = bool(delivery_info.get('redelivered'))
    counts = _record_idempotent_delivery(marker)
    if counts['deliveries'] == 1:
        time.sleep(120)
    return {
        'marker': marker,
        'redelivered': redelivered,
        **counts,
    }


@app.task(name=BEAT_PROBE_TASK_NAME, base=Task)
def beat_probe_task(marker: str) -> dict[str, int]:
    """由真实 Beat 周期投递的无副作用幂等探针。"""
    return _record_idempotent_delivery(marker)
