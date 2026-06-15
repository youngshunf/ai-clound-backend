"""M1 移动端业务埋点 ORM (B8).

依赖规范: docs/架构设计/移动端/10-观测崩溃与日志详细设计.md §6.1。
移动端 (Android / iOS) 通过 POST /api/v1/app/telemetry/events 批量上报
业务旅程事件; 友盟 U-App 作为客户端侧并行通道, 后端 /telemetry/events
作为 Grafana 聚合口。

表 `telemetry_events`:
- id (BIGINT PK, 自增)
- hasn_id (VARCHAR(40), index) — 归属 owner (服务端 JWT 反查, 客户端不传)
- event_type (VARCHAR(64), index) — §6.1 枚举 (auth.login_start / .success / .failure /
  im.conversation_opened / im.message_sent / push.wakeup / runtime.panicked /
  runtime.fatal / runtime.restarted / account.switched / account.logged_out)
- properties (JSONB, nullable) — 事件属性, 已在客户端脱敏 (§7.1 永不含消息正文 /
  owner_api_key / push_token / 密码 / 验证码)
- occurred_at (TIMESTAMPTZ, index) — 客户端触发事件的绝对时间 (从 occurred_at_unix_ms 换算)

不变式 §4 + §7.1: payload 永不含消息正文 / 凭据明文 / 任意 PII 字段。
数据保留: §14.5 约束 90 天内清理 (退役机制本 story 不做; 由 M1.5 支持)。
"""
from __future__ import annotations

import enum

from datetime import datetime  # noqa: TC003 — Mapped[datetime] resolved at runtime by SQLAlchemy

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class TelemetryEventType(str, enum.Enum):
    """§6.1 业务事件枚举 (含 runtime.* 3 条, 共 11 个枚举值).

    注: 本枚举作为 schema 白名单使用; 新增事件类型时需同步更新 10 号文档 §6.1 +
    前端 Kotlin / Swift 客户端枚举。
    """

    AUTH_LOGIN_START = 'auth.login_start'
    AUTH_LOGIN_SUCCESS = 'auth.login_success'
    AUTH_LOGIN_FAILURE = 'auth.login_failure'
    IM_CONVERSATION_OPENED = 'im.conversation_opened'
    IM_MESSAGE_SENT = 'im.message_sent'
    PUSH_WAKEUP = 'push.wakeup'
    RUNTIME_PANICKED = 'runtime.panicked'
    RUNTIME_FATAL = 'runtime.fatal'
    RUNTIME_RESTARTED = 'runtime.restarted'
    ACCOUNT_SWITCHED = 'account.switched'
    ACCOUNT_LOGGED_OUT = 'account.logged_out'


TELEMETRY_EVENT_TYPE_VALUES: frozenset[str] = frozenset(
    c.value for c in TelemetryEventType
)


class TelemetryEvent(Base):
    """移动端 telemetry_events 表 (B8)."""

    __tablename__ = 'telemetry_events'
    __table_args__ = (
        sa.Index('ix_telemetry_events_hasn_id', 'hasn_id'),
        sa.Index('ix_telemetry_events_event_type', 'event_type'),
        sa.Index('ix_telemetry_events_occurred_at', 'occurred_at'),
        {'comment': 'M1 移动端业务埋点表 (友盟 U-App 并行双写, §6.1)'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    hasn_id: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default='', comment='归属 owner 的 hasn_id'
    )
    event_type: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        default='',
        comment='事件类型 (§6.1 枚举)',
    )
    properties: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        nullable=True,
        default=None,
        comment='事件属性 JSON (已客户端脱敏, 不含 PII/凭据/正文)',
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TimeZone,
        nullable=False,
        default_factory=timezone.now,
        comment='客户端触发事件的绝对时间',
    )
