from __future__ import annotations

import inspect

from backend.app.hasn_growth.service.dispatch_service import register_channel_sender
from backend.app.hasn_growth.tasks import (
    _collection_job_for_update,
    growth_dispatch_approved_outreach,
    lead_automation_run_job,
)
from backend.app.hasn_memory.tasks import (
    owner_memory_retry_pending_merges,
    peer_portrait_sweep,
)
from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE


def test_non_transactional_external_tasks_keep_early_ack() -> None:
    tasks = (
        lead_automation_run_job,
        growth_dispatch_approved_outreach,
        owner_memory_retry_pending_merges,
        peer_portrait_sweep,
    )

    for task in tasks:
        assert task.acks_late is False
        assert task.reject_on_worker_lost is False


def test_collection_job_claim_uses_database_row_lock() -> None:
    statement = _collection_job_for_update(42)

    assert 'FOR UPDATE' in str(statement)


def test_pending_collection_jobs_have_periodic_recovery_dispatch() -> None:
    schedule = LOCAL_BEAT_SCHEDULE['获客-采集 pending 作业恢复投递']

    assert schedule['task'] == 'lead_automation_reconcile_pending'


def test_automatic_channel_sender_requires_idempotency_guarantee() -> None:
    parameter = inspect.signature(register_channel_sender).parameters['guarantees_idempotency']

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
