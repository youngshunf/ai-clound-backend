"""P0 HASN sync/runtime-report schemas.

These public schemas mirror docs/openapi-hasn-cloud-v1.yaml and add only
server-side optional redacted summary fields needed by the P0 backend service.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.app.hasn.schema.hasn_message_hub import ErrorObject
from backend.common.schema import SchemaBase

RuntimeStatus = Literal['missing', 'offline', 'online', 'degraded', 'failed']


class CursorMixin(SchemaBase):
    cursor: str | None = Field(default=None, description='Owner-scoped cursor, e.g. owner:h_xxx:123')


class SyncEventRecord(SchemaBase):
    event_id: str = Field(description='服务端事件 ID')
    event_type: str = Field(description='事件类型')
    revision: int = Field(ge=0, description='Owner 维度单调递增 revision')
    created_at: datetime = Field(description='事件创建时间')
    payload: dict[str, Any] = Field(default_factory=dict, description='脱敏事件载荷')


class SyncPullRequest(CursorMixin):
    owner_id: str = Field(description='Owner HASN ID')
    node_id: str | None = Field(default=None, description='拉取节点 ID；缺省时服务端可从 X-Node-Id 读取')
    limit: int = Field(default=100, ge=1, le=500, description='最大事件数')


class FullRefreshDirective(SchemaBase):
    """增量游标不可继续时返回给 daemon 的全量刷新指令。"""

    owner_id: str = Field(description='Owner HASN ID')
    reason: Literal['cursor_expired', 'cursor_ahead'] = Field(
        description='要求全量刷新的原因'
    )
    requested_revision: int = Field(ge=0, description='客户端请求的 revision')
    min_available_revision: int = Field(ge=0, description='服务端最早可用 revision')
    head_revision: int = Field(ge=0, description='服务端当前 head revision')
    required: bool = Field(default=True, description='是否必须执行全量刷新')


class SyncPullResponse(SchemaBase):
    events: list[SyncEventRecord] = Field(description='同步事件')
    next_cursor: str = Field(description='下一游标')
    has_more: bool = Field(description='是否还有更多')
    full_refresh: FullRefreshDirective | None = Field(
        default=None,
        description='增量游标失效时的显式全量刷新指令',
    )


class ClientEvent(SchemaBase):
    client_event_id: str = Field(description='客户端事件 ID')
    event_type: str = Field(description='事件类型')
    payload: dict[str, Any] = Field(default_factory=dict, description='脱敏上行载荷')
    hasn_id: str | None = Field(default=None, description='事件主体，可空时默认 owner')
    dedupe_key: str | None = Field(default=None, description='业务幂等键')


class SyncPushRequest(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    node_id: str | None = Field(default=None, description='上报 Node ID')
    events: list[ClientEvent] = Field(description='客户端 outbox 事件')


class SyncPushResponse(SchemaBase):
    accepted: int = Field(ge=0, description='已接收事件数')
    rejected: list[ErrorObject] = Field(default_factory=list, description='被拒绝事件')
    next_cursor: str = Field(description='下一游标')


class TaskRunSummaryRequest(SchemaBase):
    owner_id: str | None = Field(default=None, description='Owner HASN ID；通常来自 Agent JWT')
    task_id: str | None = Field(default=None, description='客户端任务 UUID，兼容旧 task_id')
    task_uuid: str | None = Field(default=None, description='任务 UUID')
    run_id: str | int | None = Field(default=None, description='兼容旧 run_id')
    task_run_id: str | int | None = Field(default=None, description='兼容旧 task_run_id')
    run_uuid: str | None = Field(default=None, description='运行 UUID')
    agent_id: str | None = Field(default=None, description='执行 Agent HASN ID')
    executor_node_id: str | None = Field(default=None, description='执行节点 ID')
    session_id: str | None = Field(default=None, description='任务执行 session ID')
    scheduled_fire_at: datetime | int | float | str | None = Field(default=None, description='计划触发时间')
    started_at: datetime | int | float | str | None = Field(default=None, description='开始时间')
    finished_at: datetime | int | float | str | None = Field(default=None, description='完成时间')
    status: str = Field(description='运行状态')
    output: str | None = Field(default=None, description='兼容旧最终输出')
    output_summary: str | None = Field(default=None, description='云端保存的输出摘要')
    error: str | None = Field(default=None, description='错误摘要')
    deep_link: str | None = Field(default=None, description='本地/云端投影链接')
    dedupe_key: str | None = Field(default=None, description='运行摘要幂等键')
    model: str | None = Field(default=None, description='执行模型')
    token_usage: dict[str, Any] | None = Field(default=None, description='Token 消耗摘要')
    duration_ms: int | None = Field(default=None, ge=0, description='执行耗时（毫秒）')


class TaskRunSummaryResponse(SchemaBase):
    run_uuid: str = Field(description='运行 UUID')
    task_uuid: str = Field(description='任务 UUID')
    owner_id: str = Field(description='Owner HASN ID')
    agent_id: str = Field(description='执行 Agent HASN ID')
    session_id: str | None = Field(default=None, description='任务执行 session ID')
    dedupe_key: str = Field(description='运行摘要幂等键')
    status: str = Field(description='运行状态')
    output_summary: str | None = Field(default=None, description='云端保存的输出摘要')
    error: str | None = Field(default=None, description='错误摘要')
    deep_link: str | None = Field(default=None, description='本地/云端投影链接')
    model: str | None = Field(default=None, description='执行模型')
    token_usage: dict[str, Any] | None = Field(default=None, description='Token 消耗摘要')
    duration_ms: int | None = Field(default=None, description='执行耗时（毫秒）')


class MemorySyncNamespaceSelector(SchemaBase):
    sync_scope_kind: Literal['owner', 'agent'] = Field(description='同步分区类型')
    names: list[str] = Field(description='需要拉取的记忆 namespace')


class MemorySyncCursor(SchemaBase):
    sync_scope_kind: Literal['owner', 'agent'] = Field(description='同步分区类型')
    sync_scope_id: str = Field(description='同步分区 ID')
    namespace: str = Field(description='记忆 namespace')
    last_pulled_revision: int = Field(default=0, ge=0, description='该 namespace 已应用到本地的 revision')


class MemorySyncPullRequest(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    agent_ids: list[str] = Field(default_factory=list, description='参与 agent 级同步的 Agent HASN ID 列表')
    namespaces: list[MemorySyncNamespaceSelector] = Field(description='按同步分区类型选择的 namespace 集合')
    cursors: list[MemorySyncCursor] = Field(default_factory=list, description='客户端持久化的 namespace 游标')
    max_events: int = Field(default=500, ge=1, le=500, description='最大返回事件数')


class MemorySyncPullResponse(SchemaBase):
    events: list[SyncEventRecord] = Field(description='按 namespace revision 过滤后的记忆事件')
    next_cursors: list[MemorySyncCursor] = Field(description='每个请求 namespace 的下一游标')
    has_more: bool = Field(description='是否还有更多事件')


class RuntimeSummary(SchemaBase):
    agent_id: str = Field(description='Agent HASN ID')
    binding_id: str | None = Field(default=None, description='脱敏公共 Binding ID')
    runtime_type: str = Field(description='Runtime 类型')
    status: RuntimeStatus = Field(description='Runtime 摘要状态')
    adapter_registered: bool = Field(default=True, description='RuntimeAdapter 是否已注册')
    handle_available: bool = Field(default=True, description='RuntimeHandle 是否可调度')
    last_seen_at: datetime | None = Field(default=None, description='最后可见时间')
    runtime_revision: int = Field(default=1, ge=0, description='Runtime 摘要修订号')
    summary_json: dict[str, Any] = Field(default_factory=dict, description='脱敏 Runtime 摘要')


class RuntimeReportRequest(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    node_id: str = Field(description='上报 Node ID')
    runtime_summaries: list[RuntimeSummary] = Field(description='Runtime 摘要列表')


class RuntimeReportResponse(SchemaBase):
    accepted: int = Field(ge=0, description='已接收摘要数量')
    rejected: list[ErrorObject] = Field(default_factory=list, description='被拒绝摘要')
    next_cursor: str = Field(description='同步游标')
