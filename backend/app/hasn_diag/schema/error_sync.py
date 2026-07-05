"""错误上行端点 `POST /api/v1/diag/app/errors:sync` 的请求/响应契约（doc21 §7）。

与 daemon 侧严格一致：occurred_at 是 unix 秒整数（daemon BIGINT 约定），云端转 timestamptz；
severity 用 `warn`（非 warning）；响应逐事件回显 local_event_id（daemon 按它对账，不靠顺序）。
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_EVENTS_PER_SYNC = 100


class DiagErrorEvent(BaseModel):
    """单条上行错误事件。"""

    local_event_id: str = Field(..., max_length=96, description='daemon 本地事件 id（对账用，非落库键）')
    source: Literal['daemon', 'hermes', 'runtime', 'webui'] = Field(..., description='来源')
    severity: Literal['critical', 'error', 'warn'] = Field(..., description='严重度')
    fingerprint: str = Field(..., min_length=1, max_length=64, description='归类键（模块级位置·无行号）')
    dedup_key: str = Field(..., min_length=1, max_length=96, description='单次物理发生幂等键')
    error_class: str | None = Field(None, max_length=128)
    message: str = Field('', description='脱敏后错误消息')
    location: str | None = Field(None, max_length=256)
    context: dict = Field(default_factory=dict)
    occurred_at: int = Field(..., description='客户端真实发生时刻（unix 秒）')
    suppressed_count: int = Field(0, ge=0, description='daemon 端采样被抑制的同 fingerprint 次数')

    def occurred_at_dt(self) -> datetime:
        return datetime.fromtimestamp(self.occurred_at, tz=dt_timezone.utc)


class DiagErrorSyncRequest(BaseModel):
    """错误批量上行请求。"""

    node_id: str = Field(..., min_length=1, max_length=64)
    app_version: str | None = Field(None, max_length=32)
    platform: str | None = Field(None, max_length=32)
    events: list[DiagErrorEvent] = Field(..., description='本批错误事件（<=100）')

    @field_validator('events')
    @classmethod
    def _cap_events(cls, v: list[DiagErrorEvent]) -> list[DiagErrorEvent]:
        if len(v) > MAX_EVENTS_PER_SYNC:
            raise ValueError(f'单批最多 {MAX_EVENTS_PER_SYNC} 条事件')
        return v


class DiagErrorSyncResult(BaseModel):
    """单事件落库结果。"""

    local_event_id: str
    accepted: bool
    deduped: bool


class DiagErrorSyncResponse(BaseModel):
    """错误批量上行响应。"""

    results: list[DiagErrorSyncResult]
