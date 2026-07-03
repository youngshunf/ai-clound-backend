from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnContentTopicsSchemaBase(SchemaBase):
    """内容与话题关联基础模型"""
    topic_id: str = Field(description='关联话题 topic_id')
    content_type: str = Field(description='内容类型 (post:帖子/article:文章)')
    content_id: str = Field(description='内容 ID（post_id 或 article_id）')
    owner_hasn_id: str = Field(description='内容责任主体 hasn_id，便于按主体过滤/治理')


class CreateHasnContentTopicsParam(HasnContentTopicsSchemaBase):
    """创建内容与话题关联参数"""


class UpdateHasnContentTopicsParam(HasnContentTopicsSchemaBase):
    """更新内容与话题关联参数"""


class DeleteHasnContentTopicsParam(SchemaBase):
    """删除内容与话题关联参数"""

    pks: list[int] = Field(description='内容与话题关联 ID 列表')


class GetHasnContentTopicsDetail(HasnContentTopicsSchemaBase):
    """内容与话题关联详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
