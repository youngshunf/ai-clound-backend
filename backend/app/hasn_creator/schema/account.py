from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class AccountSchemaBase(SchemaBase):
    """平台账号（1:N project）；同一项目多平台真实账号基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    platform: str = Field(description='平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    platform_uid: str | None = Field(None, description='None')
    nickname: str | None = Field(None, description='None')
    avatar_url: str | None = Field(None, description='None')
    bio: str | None = Field(None, description='None')
    home_url: str | None = Field(None, description='None')
    followers: int = Field(description='None')
    following: int = Field(description='None')
    total_likes: int = Field(description='None')
    total_favorites: int = Field(description='None')
    total_comments: int = Field(description='None')
    total_posts: int = Field(description='None')
    metrics_json: dict = Field(description='None')
    metrics_updated_at: datetime | None = Field(None, description='None')
    auth_status: str = Field(description='发布授权 (not_configured:未配置:gray/active:已授权:green/expired:已过期:red)')
    is_primary: bool = Field(description='None')
    notes: str | None = Field(None, description='None')


class CreateAccountParam(AccountSchemaBase):
    """创建平台账号（1:N project）；同一项目多平台真实账号参数"""


class UpdateAccountParam(AccountSchemaBase):
    """更新平台账号（1:N project）；同一项目多平台真实账号参数"""


class DeleteAccountParam(SchemaBase):
    """删除平台账号（1:N project）；同一项目多平台真实账号参数"""

    pks: list[int] = Field(description='平台账号（1:N project）；同一项目多平台真实账号 ID 列表')


class GetAccountDetail(AccountSchemaBase):
    """平台账号（1:N project）；同一项目多平台真实账号详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
