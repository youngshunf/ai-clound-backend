from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnTopicsSchemaBase(SchemaBase):
    """社区话题实体基础模型"""
    topic_id: str = Field(description='全局唯一 ID，格式 tpc_{nanoid}')
    name: str = Field(description='展示名（可改）')
    slug: str = Field(description='URL 友好标识，公开路由 /community/topics/{slug}，改名不改 slug')
    description: str | None = Field(None, description='话题描述')
    cover_url: str | None = Field(None, description='封面图 URL')
    status: str = Field(description='状态 (active:正常:green/merged:已合并:gray/archived:已归档:orange/blocked:已封禁:red)')
    merged_into_topic_id: str | None = Field(None, description='status=merged 时指向合并目标 topic_id')
    is_featured: bool = Field(description='运营置顶/推荐')
    is_official: bool = Field(description='官方话题标识')
    created_by_hasn_id: str | None = Field(None, description='创建者 hasn_id（用户自建或运营建，可空=系统归一生成）')
    content_count: int = Field(description='关联内容数（冗余，异步维护）')
    follow_count: int = Field(description='关注数（冗余，异步维护）')
    view_count: int = Field(description='浏览数（冗余，异步维护）')
    last_active_time: datetime | None = Field(None, description='最近活跃时间')


class CreateHasnTopicsParam(HasnTopicsSchemaBase):
    """创建社区话题实体参数"""


class UpdateHasnTopicsParam(HasnTopicsSchemaBase):
    """更新社区话题实体参数"""


class DeleteHasnTopicsParam(SchemaBase):
    """删除社区话题实体参数"""

    pks: list[int] = Field(description='社区话题实体 ID 列表')


class GetHasnTopicsDetail(HasnTopicsSchemaBase):
    """社区话题实体详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
