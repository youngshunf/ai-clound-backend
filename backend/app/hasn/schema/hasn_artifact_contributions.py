from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnArtifactContributionsSchemaBase(SchemaBase):
    """Agent 对产物的不可变参与记录基础模型"""
    contribution_id: str = Field(description='参与记录公开标识')
    artifact_id: str = Field(description='关联产物当前态公开标识')
    owner_hasn_id: str = Field(description='主人隔离键')
    agent_hasn_id: str = Field(description='参与分身标识')
    work_session_id: str | None = Field(None, description='本次参与所属工作会话')
    project_id: str | UUID | None = Field(None, description='本次参与所属平台项目')
    action: str = Field(description='参与动作 (create:新增:update:修改)')
    source_kind: str = Field(description='参与来源 (app_write:应用写入:platform_tool:平台工具:runtime_file:运行时文件:agent_note:分身自撰:external_import:外部导入)')
    source_tool: str | None = Field(None, description='实际写工具或处理器名称')
    source_app_id: str | None = Field(None, description='本次操作所在应用上下文')
    dispatch_id: str | None = Field(None, description='派发关联标识')
    tool_call_id: str | None = Field(None, description='工具调用标识')
    source_event_id: str | None = Field(None, description='来源事件标识')
    idempotency_key: str = Field(description='来源幂等键')
    conversation_id: str | UUID | None = Field(None, description='来源会话标识')
    message_id: int | None = Field(None, description='来源消息标识')
    occurred_time: datetime = Field(description='真实写入或后置核验完成时间')
    meta_data: dict = Field(description='不含正文和本地绝对路径的上下文快照')


class CreateHasnArtifactContributionsParam(HasnArtifactContributionsSchemaBase):
    """创建Agent 对产物的不可变参与记录参数"""


class UpdateHasnArtifactContributionsParam(HasnArtifactContributionsSchemaBase):
    """更新Agent 对产物的不可变参与记录参数"""


class DeleteHasnArtifactContributionsParam(SchemaBase):
    """删除Agent 对产物的不可变参与记录参数"""

    pks: list[int] = Field(description='Agent 对产物的不可变参与记录 ID 列表')


class GetHasnArtifactContributionsDetail(HasnArtifactContributionsSchemaBase):
    """Agent 对产物的不可变参与记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
