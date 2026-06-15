from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class TopicSchemaBase(SchemaBase):
    """选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    title: str = Field(description='None')
    potential_score: float = Field(description='None')
    heat_index: float = Field(description='None')
    reason: str | None = Field(None, description='None')
    keywords: dict = Field(description='None')
    creative_angles: dict = Field(description='None')
    status: int = Field(description='状态 (0:待处理:gray/1:已采纳:green/2:已跳过:red)')
    content_id: int | None = Field(None, description='采纳后关联内容（content.id 逻辑引用）')
    batch_date: str | None = Field(None, description='None')
    source_uid: str | None = Field(None, description='None')


class CreateTopicParam(TopicSchemaBase):
    """创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数"""


class UpdateTopicParam(TopicSchemaBase):
    """更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数"""


class DeleteTopicParam(SchemaBase):
    """删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数"""

    pks: list[int] = Field(description='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID 列表')


class GetTopicDetail(TopicSchemaBase):
    """选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
