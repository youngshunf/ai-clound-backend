"""平台账号 Schema 定义"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.common.schema import SchemaBase


class PlatformAccountBase(SchemaBase):
    platform: str = Field(description='平台类型')
    account_id: str = Field(description='平台侧账号ID')
    account_name: str | None = Field(None, description='账号名称')
    avatar_url: str | None = Field(None, description='头像URL')
    followers_count: int | None = Field(0, description='粉丝数')
    following_count: int | None = Field(0, description='关注数')
    posts_count: int | None = Field(0, description='作品数')
    is_active: bool | None = Field(True, description='是否启用')
    session_valid: bool | None = Field(False, description='登录凭证是否有效')
    metadata_info: dict | None = Field(default_factory=dict, description='元数据')


class PlatformAccountCreate(PlatformAccountBase):
    project_id: str = Field(description='关联项目ID')


class PlatformAccountUpdate(BaseModel):
    account_name: str | None = None
    avatar_url: str | None = None
    followers_count: int | None = None
    following_count: int | None = None
    posts_count: int | None = None
    is_active: bool | None = None
    session_valid: bool | None = None
    metadata_info: dict | None = None


class PlatformAccountSync(PlatformAccountBase):
    """同步请求使用的 Schema，允许更新所有字段"""
    project_id: str = Field(description='项目 ID (UUID)')
    id: str | None = Field(None, description='账号 ID (UUID), 客户端可指定')
    is_deleted: bool | None = False
    deleted_at: datetime | None = None


class PlatformAccountInfo(PlatformAccountBase):
    id: str
    project_id: str
    user_id: str
    is_deleted: bool
    last_sync_at: datetime | None
    server_version: int
    created_time: datetime
    updated_time: datetime | None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
