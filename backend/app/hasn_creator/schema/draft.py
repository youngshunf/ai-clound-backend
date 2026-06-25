from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class DraftSchemaBase(SchemaBase):
    """草稿箱（灵感快速捕获，轻量独立于正式流水线）基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    title: str | None = Field(None, description='None')
    content: str | None = Field(None, description='None')
    media: dict = Field(description='媒体引用（hasn://asset/）')
    tags: dict = Field(description='None')
    target_platforms: dict = Field(description='None')


class CreateDraftParam(DraftSchemaBase):
    """创建草稿箱（灵感快速捕获，轻量独立于正式流水线）参数"""


class UpdateDraftParam(DraftSchemaBase):
    """更新草稿箱（灵感快速捕获，轻量独立于正式流水线）参数"""


class DeleteDraftParam(SchemaBase):
    """删除草稿箱（灵感快速捕获，轻量独立于正式流水线）参数"""

    pks: list[int] = Field(description='草稿箱（灵感快速捕获，轻量独立于正式流水线） ID 列表')


class GetDraftDetail(DraftSchemaBase):
    """草稿箱（灵感快速捕获，轻量独立于正式流水线）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
