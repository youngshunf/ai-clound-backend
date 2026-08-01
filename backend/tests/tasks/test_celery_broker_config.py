from __future__ import annotations

from importlib.metadata import version

import pytest

from kombu import Queue

from backend.app.task import celery as celery_module
from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE
from backend.core.conf import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    for name, value in overrides.items():
        monkeypatch.setenv(name, str(value))
    return Settings()


def test_celery_dependency_is_locked_to_verified_release() -> None:
    assert version('celery') == '5.6.3'


def test_rabbitmq_broker_url_uses_shared_safe_dsn_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(
        monkeypatch,
        CELERY_BROKER='rabbitmq',
        CELERY_RABBITMQ_HOST='rabbit.internal',
        CELERY_RABBITMQ_PORT=5673,
        CELERY_RABBITMQ_VHOST='team/blue',
        CELERY_RABBITMQ_USERNAME='celery@huanxing',
        CELERY_RABBITMQ_PASSWORD='secret:/%',
    )

    assert celery_module.build_celery_broker_url(configured) == (
        'amqp://celery%40huanxing:secret%3A%2F%25@rabbit.internal:5673/team%2Fblue'
    )


def test_redis_rollback_broker_url_encodes_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(
        monkeypatch,
        CELERY_BROKER='redis',
        REDIS_HOST='127.0.0.1',
        REDIS_PORT=6379,
        REDIS_PASSWORD='secret:/%',
        CELERY_BROKER_REDIS_DATABASE=1,
    )

    assert celery_module.build_celery_broker_url(configured) == ('redis://:secret%3A%2F%25@127.0.0.1:6379/1')


def test_rabbitmq_broker_configuration_is_durable_and_at_least_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(
        monkeypatch,
        CELERY_BROKER='rabbitmq',
        CELERY_RABBITMQ_USERNAME='huanxing_celery',
        CELERY_RABBITMQ_PASSWORD='valid-secret',
    )

    options = celery_module.build_celery_broker_options(configured)
    queues = options['task_queues']
    assert isinstance(queues, tuple)
    queue = queues[0]
    assert isinstance(queue, Queue)

    assert options['task_default_queue'] == 'huanxing.celery.default'
    assert options['task_default_exchange'] == 'huanxing.celery'
    assert options['task_default_exchange_type'] == 'direct'
    assert options['task_default_routing_key'] == 'huanxing.celery.default'
    assert options['task_default_delivery_mode'] == 'persistent'
    assert options['task_create_missing_queues'] is False
    assert queue.name == 'huanxing.celery.default'
    assert queue.durable is True
    assert queue.auto_delete is False
    assert queue.queue_arguments == {'x-queue-type': 'classic'}
    assert queue.exchange.name == 'huanxing.celery'
    assert queue.exchange.type == 'direct'
    assert queue.exchange.durable is True
    assert queue.routing_key == 'huanxing.celery.default'

    assert options['broker_transport_options'] == {'confirm_publish': True}
    assert options['broker_heartbeat'] == 60
    assert options['broker_heartbeat_checkrate'] == pytest.approx(2.0)
    assert options['broker_connection_timeout'] == 10
    assert options['broker_connection_retry'] is True
    assert options['broker_connection_retry_on_startup'] is True
    assert options['broker_channel_error_retry'] is True
    assert options['broker_connection_max_retries'] == 100
    assert options['worker_prefetch_multiplier'] == 1
    assert options['task_acks_late'] is True
    assert options['task_acks_on_failure_or_timeout'] is True
    assert options['task_reject_on_worker_lost'] is True
    assert options['control_queue_exclusive'] is True
    assert options['control_queue_durable'] is False
    assert options['event_queue_exclusive'] is True
    assert options['event_queue_durable'] is False


def test_redis_rollback_keeps_explicit_queue_without_rabbit_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(monkeypatch, CELERY_BROKER='redis')

    options = celery_module.build_celery_broker_options(configured)

    assert options['task_default_queue'] == 'huanxing.celery.rollback'
    assert options['task_default_exchange'] == 'huanxing.celery.rollback'
    assert options['task_default_routing_key'] == 'huanxing.celery.rollback'
    assert options['broker_transport_options'] == {}
    assert options['broker_heartbeat'] is None


def test_production_task_discovery_and_beat_schedule_exclude_demo_tasks() -> None:
    retired_tasks = {'task_demo', 'task_demo_async', 'task_demo_params'}
    scheduled_tasks = {str(entry['task']) for entry in LOCAL_BEAT_SCHEDULE.values()}

    assert scheduled_tasks.isdisjoint(retired_tasks)
    assert 'hasn_offline_shadow_reconcile' in scheduled_tasks
    assert 'backend.app.task.tasks' not in celery_module.find_task_packages()
