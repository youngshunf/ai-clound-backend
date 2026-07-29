from __future__ import annotations

from backend.app.tasks.push_message import push_message


def test_legacy_push_message_task_is_registered_as_idempotent_noop() -> None:
    assert push_message.name == 'push_message'
    assert push_message.acks_late is True
    assert push_message.reject_on_worker_lost is True
    assert push_message.run(12345) == 'retired'
