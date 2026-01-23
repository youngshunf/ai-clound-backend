"""用户 API Key Schema"""

from datetime import datetime

from pydantic import Field

from backend.app.llm.enums import ApiKeyStatus
from backend.common.schema import SchemaBase


class UserApiKeyBase(SchemaBase):
    """用户 API Key 基础 Schema"""

    name: str = Field(description='Key 名称')
    expires_at: datetime | None = Field(default=None, description='过期时间')
    rate_limit_config_id: int | None = Field(default=None, description='速率限制配置 ID')
    custom_daily_tokens: int | None = Field(default=None, description='自定义日 Token 限制')
    custom_monthly_tokens: int | None = Field(default=None, description='自定义月 Token 限制')
    custom_rpm_limit: int | None = Field(default=None, description='自定义 RPM 限制')
    allowed_models: list[int] | None = Field(default=None, description='允许的模型 ID 列表')
    metadata_: dict | None = Field(default=None, alias='metadata', description='元数据')


class CreateUserApiKeyParam(UserApiKeyBase):
    """创建用户 API Key 参数"""


class CreateUserApiKeyResponse(SchemaBase):
    """创建用户 API Key 响应"""

    id: int
    name: str
    key_prefix: str
    api_key: str = Field(description='完整 API Key (仅在创建时返回一次)')
    expires_at: datetime | None = None


class UpdateUserApiKeyParam(SchemaBase):
    """更新用户 API Key 参数"""

    name: str | None = Field(default=None, description='Key 名称')
    status: ApiKeyStatus | None = Field(default=None, description='状态')
    expires_at: datetime | None = Field(default=None, description='过期时间')
    rate_limit_config_id: int | None = Field(default=None, description='速率限制配置 ID')
    custom_daily_tokens: int | None = Field(default=None, description='自定义日 Token 限制')
    custom_monthly_tokens: int | None = Field(default=None, description='自定义月 Token 限制')
    custom_rpm_limit: int | None = Field(default=None, description='自定义 RPM 限制')
    allowed_models: list[int] | None = Field(default=None, description='允许的模型 ID 列表')
    metadata_: dict | None = Field(default=None, alias='metadata', description='元数据')


class GetUserApiKeyDetail(SchemaBase):
    """用户 API Key 详情"""

    id: int
    user_id: int
    name: str
    key_prefix: str
    status: str
    expires_at: datetime | None = None
    rate_limit_config_id: int | None = None
    custom_daily_tokens: int | None = None
    custom_monthly_tokens: int | None = None
    custom_rpm_limit: int | None = None
    allowed_models: list[int] | None = None
    metadata_: dict | None = Field(default=None, alias='metadata')
    last_used_at: datetime | None = None
    created_time: datetime


class GetUserApiKeyList(SchemaBase):
    """用户 API Key 列表项"""

    id: int
    name: str
    key_prefix: str
    status: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_time: datetime
