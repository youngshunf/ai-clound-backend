from __future__ import annotations

import inspect

import pytest

from backend.app.hasn_growth.service.dispatch_service import register_channel_sender
from backend.app.hasn_growth.tasks import (
    _collection_job_for_update,
    growth_dispatch_approved_outreach,
    lead_automation_run_job,
)
from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE


def test_non_transactional_external_tasks_keep_early_ack() -> None:
    tasks = (
        lead_automation_run_job,
        growth_dispatch_approved_outreach,
    )

    for task in tasks:
        assert task.acks_late is False
        assert task.reject_on_worker_lost is False


def test_cloud_memory_semantic_workers_are_retired() -> None:
    """doc19 §10 退役回归：云端记忆语义处理的 celery 面必须真的不存在。

    退役如果只是「不再调用」而留着死函数与 beat 条目，某次运维重启 worker 就会把云端 LLM
    合并重新跑起来——与主脑合并双写同一份 `owner_memory`，谁覆盖谁全看时序。这里两头都钉死：
    模块级 `hasn_memory.tasks` 已删除、beat 里也不许再出现这两个任务名。
    """
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('backend.app.hasn_memory.tasks')

    scheduled_task_names = {entry['task'] for entry in LOCAL_BEAT_SCHEDULE.values()}
    assert 'owner_memory_retry_pending_merges' not in scheduled_task_names
    assert 'peer_portrait_sweep' not in scheduled_task_names


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
