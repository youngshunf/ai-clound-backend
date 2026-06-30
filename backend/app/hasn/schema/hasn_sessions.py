from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSessionsSchemaBase(SchemaBase):
    """HASN 会话分层 - 逻辑会话基础模型"""
    conversation_id: str | UUID | None = Field(None, description='关联的 conversation ID')
    session_kind: str = Field(description='会话类型 (conversation/task/temporary/external/system)')
    session_scope: str = Field(description='同步范围 (conversation_visible/summary_only/local_only)')
    session_status: str = Field(description='会话状态 (active/paused/completed/archived)')
    origin_type: str | None = Field(None, description='来源类型 (ui/scheduler/external_app/api/system/task_run/workflow_run/app/manual/copilot)；app=AI-Native 应用工作会话；manual=派发型工作会话；copilot=会议副驾会话')
    origin_ref: str | None = Field(None, description='来源引用 (task_id/app_id/trace_id)；origin_type=app 时形如 resource:<app>:<id>')
    parent_session_id: str | None = Field(None, description='父会话 ID (用于分叉)')
    fork_point_message_id: int | None = Field(None, description='分叉点消息 ID')
    summary_checkpoint_json: str | None = Field(None, description='会话摘要检查点 (JSON)')
    last_message_id: int | None = Field(None, description='最后一条消息 ID')
    last_message_at: datetime | None = Field(None, description='最后消息时间')
    message_count: int = Field(description='消息总数')


class CreateHasnSessionsParam(HasnSessionsSchemaBase):
    """创建HASN 会话分层 - 逻辑会话参数"""


class UpdateHasnSessionsParam(HasnSessionsSchemaBase):
    """更新HASN 会话分层 - 逻辑会话参数"""


class DeleteHasnSessionsParam(SchemaBase):
    """删除HASN 会话分层 - 逻辑会话参数"""

    pks: list[int] = Field(description='HASN 会话分层 - 逻辑会话 ID 列表')


class GetHasnSessionsDetail(HasnSessionsSchemaBase):
    """HASN 会话分层 - 逻辑会话详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
