from __future__ import annotations

import re

from typing import Any


EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'(?:\+\d{8,15}|\b1[3-9]\d{9}\b)')


class AuditPayloadLeakError(ValueError):
    pass


def assert_audit_payload_safe(payload: dict[str, Any]) -> None:
    text = str(payload)
    if EMAIL_RE.search(text) or PHONE_RE.search(text):
        raise AuditPayloadLeakError('audit payload contains plaintext PII')


def log_event(
    *,
    event_type: str,
    actor_user_id: int | None,
    actor_role: str,
    target_table: str,
    target_count: int,
    target_ref: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    assert_audit_payload_safe(payload)
    return {
        'event_type': event_type,
        'actor_user_id': actor_user_id,
        'actor_role': actor_role,
        'target_table': target_table,
        'target_count': target_count,
        'target_ref': target_ref,
        'payload': payload,
        'result': 'success',
    }
