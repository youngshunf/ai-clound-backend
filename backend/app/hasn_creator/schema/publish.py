from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PublishSchemaBase(SchemaBase):
    """发布记录（= content × account：发到某平台账号 + 数据指标）基础模型"""
    content_id: int = Field(description='None')
    account_id: int = Field(description='None')
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    platform: str = Field(description='None')
    method: str = Field(description='方式 (manual_assist:人工辅助:gray/api_auto:API自动:green)')
    status: str = Field(description='状态 (draft:草稿:gray/pending_review:待审:orange/approved:已通过:blue/publishing:发布中:cyan/published:已发布:green/failed:失败:red)')
    publish_url: str | None = Field(None, description='发布链接（主人回填 / 系统回填）')
    publish_note: str | None = Field(None, description='给主人看：发布建议（最佳时间/话题标签/置顶评论）')
    approval_user_id: int | None = Field(None, description='None')
    approved_at: datetime | None = Field(None, description='None')
    error_message: str | None = Field(None, description='失败如实回报（零 fake）')
    published_at: datetime | None = Field(None, description='None')
    views: int = Field(description='None')
    likes: int = Field(description='None')
    comments: int = Field(description='None')
    shares: int = Field(description='None')
    favorites: int = Field(description='None')
    new_followers: int = Field(description='None')
    metrics_json: dict = Field(description='None')
    metrics_updated_at: datetime | None = Field(None, description='None')


class CreatePublishParam(PublishSchemaBase):
    """创建发布记录（= content × account：发到某平台账号 + 数据指标）参数"""


class UpdatePublishParam(PublishSchemaBase):
    """更新发布记录（= content × account：发到某平台账号 + 数据指标）参数"""


class DeletePublishParam(SchemaBase):
    """删除发布记录（= content × account：发到某平台账号 + 数据指标）参数"""

    pks: list[int] = Field(description='发布记录（= content × account：发到某平台账号 + 数据指标） ID 列表')


class GetPublishDetail(PublishSchemaBase):
    """发布记录（= content × account：发到某平台账号 + 数据指标）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
