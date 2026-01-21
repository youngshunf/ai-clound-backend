"""平台账号 Schema 定义"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.common.schema import SchemaBase


class PlatformAccountBase(SchemaBase):
    platform: str = Field(description='平台类型')
    account_id: str = Field(description='平台侧账号ID')
    account_name: Optional[str] = Field(None, description='账号名称')
    avatar_url: Optional[str] = Field(None, description='头像URL')
    followers_count: Optional[int] = Field(0, description='粉丝数')
    following_count: Optional[int] = Field(0, description='关注数')
    posts_count: Optional[int] = Field(0, description='作品数')
    is_active: Optional[bool] = Field(True, description='是否启用')
    session_valid: Optional[bool] = Field(False, description='登录凭证是否有效')
    metadata_info: Optional[dict] = Field(default_factory=dict, description='元数据')


class PlatformAccountCreate(PlatformAccountBase):
    project_id: int = Field(description='关联项目ID')


class PlatformAccountUpdate(BaseModel):
    account_name: Optional[str] = None
    avatar_url: Optional[str] = None
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    posts_count: Optional[int] = None
    is_active: Optional[bool] = None
    session_valid: Optional[bool] = None
    metadata_info: Optional[dict] = None


class PlatformAccountSync(PlatformAccountBase):
    """同步请求使用的 Schema，允许更新所有字段"""
    project_id: int
    is_deleted: Optional[bool] = False
    deleted_at: Optional[datetime] = None


class PlatformAccountInfo(PlatformAccountBase):
    id: int
    uuid: str
    project_id: int
    project_uuid: str
    user_id: int
    is_deleted: bool
    last_sync_at: Optional[datetime]
    server_version: int
    created_time: datetime
    updated_time: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)
