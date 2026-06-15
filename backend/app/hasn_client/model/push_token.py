"""M1 移动端推送 Token ORM.

依赖规范: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §5.3。
M1 决策: channel 字段枚举固定 `umeng_push` (友盟 U-Push 已内聚小米/华为/OPPO/
vivo/魅族/Meizu 等厂商通道); FCM/其他 channel 延期到 M2。

表 `push_tokens`:
- id (BIGINT PK, 自增)
- hasn_id (VARCHAR(40), index) — 归属 owner
- device_id (VARCHAR(64)) — 唯一设备标识
- channel (VARCHAR(16)) — M1 固定为 'umeng_push'
- token (VARCHAR(512)) — 通道 push token
- registered_at (TIMESTAMPTZ, default now) — 首次注册时间
- last_seen_at (TIMESTAMPTZ, default now) — 最后一次心跳/注册时间

唯一索引: (hasn_id, device_id, channel) — 同一设备同一通道仅一行。

Alembic 迁移: backend/alembic/versions/20260421_b3_create_push_tokens.py
"""
from __future__ import annotations

import enum

from datetime import datetime  # noqa: TC003 — Mapped[datetime] is resolved at runtime by SQLAlchemy

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_client.model._encryption import EncryptedToken
from backend.app.hasn_client.model.push_token_audit import PushTokenAudit, register_audit_listeners
from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class PushChannel(str, enum.Enum):
    """M1 推送通道枚举.

    固定为 'umeng_push' — 友盟 U-Push 已内聚 6 厂商 (小米/华为/OPPO/vivo/魅族/Meizu)
    + FCM 海外通道由 M2 再开第二枚举值; 单独接 FCM / 个推 / 各厂商 SDK 为反模式
    (见 scripts/ralph-B/CLAUDE.md §M1 技术栈约束)。
    """

    UMENG_PUSH = 'umeng_push'


PUSH_CHANNEL_VALUES: frozenset[str] = frozenset(c.value for c in PushChannel)


class PushToken(Base):
    """移动端 push_tokens 表 (B3)."""

    __tablename__ = 'push_tokens'
    __table_args__ = (
        sa.UniqueConstraint(
            'hasn_id',
            'device_id',
            'channel',
            name='uq_push_tokens_hasn_device_channel',
        ),
        sa.Index('ix_push_tokens_hasn_id', 'hasn_id'),
        {'comment': 'M1 移动端推送 Token 表 (友盟 U-Push)'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    hasn_id: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default='', comment='归属 owner 的 hasn_id'
    )
    device_id: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default='', comment='唯一设备标识'
    )
    channel: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        default=PushChannel.UMENG_PUSH.value,
        comment="推送通道 (M1 固定 'umeng_push')",
    )
    token: Mapped[str] = mapped_column(
        EncryptedToken(),
        nullable=False,
        default='',
        comment='通道 push token (B10 Fernet 静态加密; 物理类型 BYTEA)',
    )
    registered_at: Mapped[datetime] = mapped_column(
        TimeZone,
        nullable=False,
        default_factory=timezone.now,
        comment='首次注册时间',
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TimeZone,
        nullable=False,
        default_factory=timezone.now,
        comment='最后一次注册/心跳时间',
    )


# B10 — attach after_insert/after_update/after_delete audit listeners.
# PushTokenAudit.__table__ receives one row per row-level mutation, same txn.
register_audit_listeners(PushToken, PushTokenAudit.__table__)
