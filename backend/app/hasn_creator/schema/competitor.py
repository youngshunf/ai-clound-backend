from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CompetitorSchemaBase(SchemaBase):
    """竞品账号（定位/选题调研输入）基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    name: str = Field(description='None')
    platform: str | None = Field(None, description='平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    url: str | None = Field(None, description='None')
    follower_count: int = Field(description='None')
    avg_likes: int = Field(description='None')
    content_style: str | None = Field(None, description='None')
    strengths: dict = Field(description='None')
    notes: str | None = Field(None, description='None')
    tags: dict = Field(description='None')
    last_analyzed: datetime | None = Field(None, description='None')


class CreateCompetitorParam(CompetitorSchemaBase):
    """创建竞品账号（定位/选题调研输入）参数"""


class UpdateCompetitorParam(CompetitorSchemaBase):
    """更新竞品账号（定位/选题调研输入）参数"""


class DeleteCompetitorParam(SchemaBase):
    """删除竞品账号（定位/选题调研输入）参数"""

    pks: list[int] = Field(description='竞品账号（定位/选题调研输入） ID 列表')


class GetCompetitorDetail(CompetitorSchemaBase):
    """竞品账号（定位/选题调研输入）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
