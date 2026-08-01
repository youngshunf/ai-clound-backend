from __future__ import annotations

import os

from backend.tests.tasks.celery_rabbitmq_e2e_worker import (
    BEAT_PROBE_TASK_NAME,
    app,
)

marker = os.getenv('CELERY_RABBITMQ_E2E_BEAT_MARKER')
queue_name = os.getenv('CELERY_RABBITMQ_E2E_QUEUE')
if not marker or not queue_name:
    raise RuntimeError('缺少真实 RabbitMQ Beat E2E 环境变量')

app.conf.update(
    beat_schedule={
        'rabbitmq-e2e-beat-probe': {
            'task': BEAT_PROBE_TASK_NAME,
            'schedule': 1.0,
            'args': (marker,),
            'options': {'queue': queue_name},
        },
    },
    beat_scheduler='backend.app.task.utils.schedulers:DatabaseScheduler',
)
