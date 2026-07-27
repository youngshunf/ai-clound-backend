"""full-refresh 契约构造。"""

from __future__ import annotations

from typing import Literal

from backend.app.hasn_sync.ports.dto import FullRefreshContract


def require_full_refresh(
    *,
    owner_id: str,
    reason: Literal['cursor_expired', 'cursor_ahead'],
    requested_revision: int,
    min_available_revision: int,
    head_revision: int,
) -> FullRefreshContract:
    """构造显式 full-refresh 指令，不静默重置客户端游标。"""
    return FullRefreshContract(
        owner_id=owner_id,
        reason=reason,
        requested_revision=requested_revision,
        min_available_revision=min_available_revision,
        head_revision=head_revision,
    )
