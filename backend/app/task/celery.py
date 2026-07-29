import os
import urllib.parse

import celery
import celery_aio_pool

from celery.app import trace as celery_trace
from celery.signals import worker_process_init
from kombu import Exchange, Queue
from opentelemetry.instrumentation.celery import CeleryInstrumentor

from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE
from backend.common.enums import DataBaseType
from backend.common.messaging.rabbitmq import build_amqp_dsn
from backend.common.observability.otel import init_resource, init_tracer
from backend.core.conf import Settings, settings
from backend.core.path_conf import BASE_PATH

CELERY_DEFAULT_QUEUE = 'huanxing.celery.default'
CELERY_DEFAULT_EXCHANGE = 'huanxing.celery'
CELERY_DEFAULT_ROUTING_KEY = CELERY_DEFAULT_QUEUE
CELERY_REDIS_ROLLBACK_QUEUE = 'celery'


@worker_process_init.connect(weak=False)
def init_celery_worker_tracing(*args, **kwargs) -> None:
    """初始化 Celery 追踪"""
    if settings.GRAFANA_METRICS_ENABLE:
        resource = init_resource('fba_celery_worker')
        init_tracer(resource)
        CeleryInstrumentor().instrument()


def _guarded_beat_schedule() -> dict:
    """返回经守卫校验的 beat 调度表。

    已退役的云端积分定时任务只要还排在表里就会继续把云端算出的余额反向覆盖回 NewAPI，
    因此这里让它启动即失败，而不是等到下一个整点才发现额度又被写回去了。
    """
    from backend.app.billing.core.retired_credit_paths import assert_no_retired_credit_tasks

    assert_no_retired_credit_tasks(LOCAL_BEAT_SCHEDULE)
    return LOCAL_BEAT_SCHEDULE


def find_task_packages() -> list[str]:
    packages = []
    # 扫描 app 下所有模块的 tasks 子目录
    app_dir = BASE_PATH / 'app'
    for root, _dirs, files in os.walk(app_dir):
        if 'tasks.py' in files:
            package = root.replace(str(BASE_PATH.parent) + os.path.sep, '').replace(os.path.sep, '.')
            packages.append(package)
    return packages


def build_celery_broker_url(config: Settings) -> str:
    """构造 Celery broker URL，不在日志中输出返回值。"""
    if config.CELERY_BROKER == 'rabbitmq':
        return build_amqp_dsn(
            host=config.CELERY_RABBITMQ_HOST,
            port=config.CELERY_RABBITMQ_PORT,
            username=config.CELERY_RABBITMQ_USERNAME,
            password=config.CELERY_RABBITMQ_PASSWORD,
            vhost=config.CELERY_RABBITMQ_VHOST,
        )
    password = urllib.parse.quote(config.REDIS_PASSWORD, safe='')
    return f'redis://:{password}@{config.REDIS_HOST}:{config.REDIS_PORT}/{config.CELERY_BROKER_REDIS_DATABASE}'


def build_celery_broker_options(config: Settings) -> dict[str, object]:
    """返回固定队列与 durable 任务至少一次投递所需的 Celery 配置。"""
    is_rabbitmq = config.CELERY_BROKER == 'rabbitmq'
    queue_name = CELERY_DEFAULT_QUEUE if is_rabbitmq else CELERY_REDIS_ROLLBACK_QUEUE
    exchange_name = CELERY_DEFAULT_EXCHANGE if is_rabbitmq else queue_name
    routing_key = CELERY_DEFAULT_ROUTING_KEY if is_rabbitmq else queue_name
    default_exchange = Exchange(
        exchange_name,
        type='direct',
        durable=True,
        auto_delete=False,
    )
    default_queue = Queue(
        queue_name,
        exchange=default_exchange,
        routing_key=routing_key,
        durable=True,
        auto_delete=False,
        queue_arguments={'x-queue-type': 'classic'},
    )
    return {
        'task_default_queue': queue_name,
        'task_default_exchange': exchange_name,
        'task_default_exchange_type': 'direct',
        'task_default_routing_key': routing_key,
        'task_default_delivery_mode': 'persistent',
        'task_queues': (default_queue,),
        'task_create_missing_queues': False,
        'broker_transport_options': {'confirm_publish': True} if is_rabbitmq else {},
        'broker_heartbeat': 60 if is_rabbitmq else None,
        'broker_heartbeat_checkrate': 2.0,
        'broker_connection_timeout': 10,
        'broker_connection_retry': True,
        'broker_connection_retry_on_startup': True,
        'broker_channel_error_retry': True,
        'broker_connection_max_retries': 100,
        'worker_prefetch_multiplier': 1,
        'task_acks_late': True,
        'task_acks_on_failure_or_timeout': True,
        'task_reject_on_worker_lost': True,
        # RabbitMQ 4.3 禁止非持久且非独占的临时队列；pidbox 与事件接收队列
        # 绑定单个客户端连接，使用独占队列可在断线时确定性清理。
        'control_queue_exclusive': True,
        'control_queue_durable': False,
        'event_queue_exclusive': True,
        'event_queue_durable': False,
    }


def init_celery() -> celery.Celery:
    """初始化 Celery 应用"""

    # TODO: Update this work if celery version >= 6.0.0
    # https://github.com/fastapi-practices/fastapi_best_architecture/issues/321
    # https://github.com/celery/celery/issues/7874
    celery_trace.build_tracer = celery_aio_pool.build_async_tracer
    celery_trace.reset_worker_optimizations()

    broker_url = build_celery_broker_url(settings)

    result_backend = f'db+postgresql+psycopg://{settings.DATABASE_USER}:{urllib.parse.quote(settings.DATABASE_PASSWORD)}@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_SCHEMA}'
    if DataBaseType.mysql == settings.DATABASE_TYPE:
        result_backend = result_backend.replace('postgresql+psycopg', 'mysql+pymysql')

    # https://docs.celeryq.dev/en/stable/userguide/configuration.html
    app = celery.Celery(
        'fba_celery',
        broker_url=broker_url,
        result_backend=result_backend,
        result_extended=True,
        database_engine_options={'echo': settings.DATABASE_ECHO},
        # result_expires=0,
        # beat_sync_every=1,
        beat_schedule=_guarded_beat_schedule(),
        beat_scheduler='backend.app.task.utils.schedulers:DatabaseScheduler',
        task_cls='backend.app.task.tasks.base:TaskBase',
        task_track_started=True,
        enable_utc=False,
        timezone=settings.DATETIME_TIMEZONE,
        worker_send_task_events=True,
        task_send_sent_event=True,
    )
    app.conf.update(build_celery_broker_options(settings))

    # 在 Celery 中设置此参数无效
    # 参数：https://github.com/celery/celery/issues/7270
    app.loader.override_backends = {'db': 'backend.app.task.database:DatabaseBackend'}

    # 自动发现任务
    packages = find_task_packages()
    app.autodiscover_tasks(packages)

    return app


# 创建 Celery 实例
celery_app: celery.Celery = init_celery()
