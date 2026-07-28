from __future__ import annotations

from typing import Any

from backend.app.hasn_growth.service.pii_boundary import (
    GrowthPiiBoundaryError,
    assert_growth_pii_payload_safe,
)


class AuditPayloadLeakError(GrowthPiiBoundaryError):
    pass


def assert_audit_payload_safe(payload: dict[str, Any]) -> None:
    try:
        assert_growth_pii_payload_safe(payload)
    except GrowthPiiBoundaryError as exc:
        raise AuditPayloadLeakError('审计载荷包含明文 PII 或禁止字段') from exc


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
