"""抑制命令放行的事务内核心操作。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnMessages, HasnSuppressedMessages
from backend.app.hasn_im.application import message_service
from backend.utils.timezone import timezone


async def commit_suppressed_rows(
    db: AsyncSession,
    rows: Iterable[HasnSuppressedMessages],
) -> list[tuple[HasnSuppressedMessages, HasnMessages, bool]]:
    """逐条把已锁定的待放行命令写成消息，并在同一事务标记抑制记录已解决。"""
    committed: list[tuple[HasnSuppressedMessages, HasnMessages, bool]] = []
    for row in rows:
        if row.message_id is not None and row.resolved_at is not None:
            existing = await db.get(HasnMessages, row.message_id)
            if existing is None:
                raise ValueError('已解决抑制记录对应的权威消息不存在')
            committed.append((row, existing, True))
            continue
        command = dict(row.command_payload or {})
        if not command:
            raise ValueError('旧抑制记录缺少可重放命令，须先执行 R3 数据迁移')
        message, deduped = await message_service.commit_released_command(db, command)
        row.message_id = message.id
        row.resolved_at = timezone.now()
        row.visible_to_owner = False
        row.dispatch_status = 'delivered'
        committed.append((row, message, deduped))
    return committed
