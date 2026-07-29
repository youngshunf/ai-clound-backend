"""同步事件保留窗口的纯判定。"""

from __future__ import annotations

from typing import Literal


def full_refresh_reason(
    *,
    requested_revision: int,
    min_available_revision: int,
    head_revision: int,
) -> Literal['cursor_expired', 'cursor_ahead'] | None:
    """判定游标是否落后保留窗口或超前于服务端 head。"""
    if requested_revision > head_revision:
        return 'cursor_ahead'
    if min_available_revision > 0 and requested_revision < min_available_revision - 1:
        return 'cursor_expired'
    return None
