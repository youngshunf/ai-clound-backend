"""速率限制配置 Schema"""

from pydantic import Field

from backend.common.schema import SchemaBase


class RateLimitConfigBase(SchemaBase):
    """速率限制配置基础 Schema"""

    name: str = Field(description='配置名称')
    daily_token_limit: int = Field(default=1000000, description='日 Token 限制')
    weekly_token_limit: int | None = Field(default=None, description='周 Token 限制')
    monthly_token_limit: int = Field(default=10000000, description='月 Token 限制')
    rpm_limit: int = Field(default=60, description='RPM 限制')
    tpm_limit: int = Field(default=100000, description='TPM 限制')
    enabled: bool = Field(default=True, description='是否启用')
    description: str | None = Field(default=None, description='描述')


class CreateRateLimitConfigParam(RateLimitConfigBase):
    """创建速率限制配置参数"""


class UpdateRateLimitConfigParam(SchemaBase):
    """更新速率限制配置参数"""

    name: str | None = Field(default=None, description='配置名称')
    daily_token_limit: int | None = Field(default=None, description='日 Token 限制')
    weekly_token_limit: int | None = Field(default=None, description='周 Token 限制')
    monthly_token_limit: int | None = Field(default=None, description='月 Token 限制')
    rpm_limit: int | None = Field(default=None, description='RPM 限制')
    tpm_limit: int | None = Field(default=None, description='TPM 限制')
    enabled: bool | None = Field(default=None, description='是否启用')
    description: str | None = Field(default=None, description='描述')


class GetRateLimitConfigDetail(RateLimitConfigBase):
    """速率限制配置详情"""

    model_config = {'from_attributes': True}

    id: int


class GetRateLimitConfigList(SchemaBase):
    """速率限制配置列表项"""

    model_config = {'from_attributes': True}

    id: int
    name: str
    daily_token_limit: int
    monthly_token_limit: int
    rpm_limit: int
    enabled: bool
    description: str | None = None
