"""B10 — push_token_audit 审计表 + SQLAlchemy 事件监听器工厂.

每次 push_tokens 行写入 / 更新 / 删除, 同事务内落一行审计
(id, push_token_id, hasn_id, device_id, channel, action, occurred_at).
目的: 满足 04 §13.2 "PII 读写审计" — 任何 push_token 变化可追溯到事件类型 + 时间.

审计表不存 token 明文/密文 (敏感数据只活在 push_tokens.token BYTEA 中), 只存元数据,
控制审计表自身的泄漏风险面。

事件监听器通过 `register_audit_listeners(token_model, audit_table)` 注册, 既能挂到
生产 `PushToken`+`PushTokenAudit.__table__`, 也能挂到测试 mirror 表 (隔离 metadata),
与 B3/B4 mirror 测试模式一致。
"""
from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Mapped[datetime] runtime-resolved by SQLAlchemy
from typing import Any

import sqlalchemy as sa

from sqlalchemy import event
from sqlalchemy import insert as sql_insert
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone
from backend.utils.timezone import timezone

AUDIT_ACTIONS: frozenset[str] = frozenset({'INSERT', 'UPDATE', 'DELETE'})


class PushTokenAudit(Base):
    """push_tokens 读写审计表 (B10)."""

    __tablename__ = 'push_token_audit'
    __table_args__ = (
        sa.Index('ix_push_token_audit_hasn_id', 'hasn_id'),
        sa.Index('ix_push_token_audit_push_token_id', 'push_token_id'),
        {'comment': 'push_tokens 读写审计表 (B10)'},
    )

    id: Mapped[int] = mapped_column(
        sa.BigInteger, primary_key=True, autoincrement=True, init=False, comment='主键 ID'
    )
    push_token_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, default=None, comment='关联 push_tokens.id (DELETE 时保留元数据)'
    )
    hasn_id: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default='', comment='归属 owner 的 hasn_id'
    )
    device_id: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default='', comment='设备 device_id'
    )
    channel: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default='umeng_push', comment='推送通道'
    )
    action: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default='INSERT', comment='INSERT/UPDATE/DELETE'
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TimeZone,
        nullable=False,
        default_factory=timezone.now,
        comment='事件时间',
    )


def register_audit_listeners(token_model: Any, audit_table: sa.Table) -> None:
    """Register after_insert/after_update/after_delete handlers on `token_model`.

    Each handler inserts one audit row into `audit_table` using the supplied
    connection (same transaction). Safe to call with either the real `PushToken`
    ORM class + `PushTokenAudit.__table__`, or with a mirror pair for isolated
    SQLite tests.
    """

    def _emit(connection: Any, target: Any, action: str) -> None:
        connection.execute(
            sql_insert(audit_table).values(
                push_token_id=getattr(target, 'id', None),
                hasn_id=getattr(target, 'hasn_id', '') or '',
                device_id=getattr(target, 'device_id', '') or '',
                channel=getattr(target, 'channel', 'umeng_push') or 'umeng_push',
                action=action,
                occurred_at=timezone.now(),
            )
        )

    @event.listens_for(token_model, 'after_insert')
    def _after_insert(mapper: Any, connection: Any, target: Any) -> None:
        _emit(connection, target, 'INSERT')

    @event.listens_for(token_model, 'after_update')
    def _after_update(mapper: Any, connection: Any, target: Any) -> None:
        _emit(connection, target, 'UPDATE')

    @event.listens_for(token_model, 'after_delete')
    def _after_delete(mapper: Any, connection: Any, target: Any) -> None:
        _emit(connection, target, 'DELETE')
