"""M1 移动端推送到达回执 ORM (B7).

依赖规范: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §12.3。
客户端收到推送后回传 {trace_id, received_at_unix_ms, channel} → 本表, 用于
计算到达率 (B9 Prometheus push_received_total / push_dispatched_total 比率)。

表 `push_receipts`:
- id (BIGINT PK, 自增)
- trace_id (VARCHAR(128), index) — 推送 trace (B6 产生, 格式 `conv:{conversation_id}`)
- hasn_id (VARCHAR(40), index) — 归属 owner (回执实际上报者, 服务器侧解析自 JWT)
- channel (VARCHAR(16)) — 对应 push_tokens.channel (M1 固定 'umeng_push')
- received_at (TIMESTAMPTZ) — 客户端收到推送的绝对时间 (从 received_at_unix_ms 换算)

不变式 §4: 推送 payload 不带消息正文, 本回执表同样不含正文字段。
"""
from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Mapped[datetime] resolved at runtime by SQLAlchemy

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class PushReceipt(Base):
    """移动端 push_receipts 表 (B7)."""

    __tablename__ = 'push_receipts'
    __table_args__ = (
        sa.Index('ix_push_receipts_trace_id', 'trace_id'),
        sa.Index('ix_push_receipts_hasn_id', 'hasn_id'),
        {'comment': 'M1 移动端推送到达回执表 (友盟 U-Push 到达率上报)'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    trace_id: Mapped[str] = mapped_column(
        sa.String(128), nullable=False, default='', comment='推送 trace (B6 生成, conv:{cid})'
    )
    hasn_id: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default='', comment='归属 owner 的 hasn_id'
    )
    channel: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        default='umeng_push',
        comment="推送通道 (M1 固定 'umeng_push')",
    )
    received_at: Mapped[datetime] = mapped_column(
        TimeZone,
        nullable=False,
        default_factory=timezone.now,
        comment='客户端收到推送的绝对时间',
    )
