"""模型供应商 Schema"""

from pydantic import Field, computed_field

from backend.app.llm.enums import ProviderType
from backend.common.schema import SchemaBase


class ProviderBase(SchemaBase):
    """供应商基础 Schema"""

    name: str = Field(description='供应商名称（自定义）')
    provider_type: ProviderType = Field(description='供应商类型（决定API格式）')
    api_base_url: str | None = Field(default=None, description='API 基础 URL')
    global_rpm_limit: int = Field(default=60, description='全局 RPM 限制')
    global_tpm_limit: int = Field(default=100000, description='全局 TPM 限制')
    enabled: bool = Field(default=True, description='是否启用')
    is_domestic: bool = Field(default=False, description='是否国内供应商')
    description: str | None = Field(default=None, description='描述')


class CreateProviderParam(ProviderBase):
    """创建供应商参数"""

    api_key: str | None = Field(default=None, description='API Key (明文，将被加密存储)')


class UpdateProviderParam(SchemaBase):
    """更新供应商参数"""

    name: str | None = Field(default=None, description='供应商名称')
    provider_type: ProviderType | None = Field(default=None, description='供应商类型')
    api_base_url: str | None = Field(default=None, description='API 基础 URL')
    api_key: str | None = Field(default=None, description='API Key (明文，将被加密存储)')
    global_rpm_limit: int | None = Field(default=None, description='全局 RPM 限制')
    global_tpm_limit: int | None = Field(default=None, description='全局 TPM 限制')
    enabled: bool | None = Field(default=None, description='是否启用')
    is_domestic: bool | None = Field(default=None, description='是否国内供应商')
    description: str | None = Field(default=None, description='描述')


class GetProviderDetail(ProviderBase):
    """供应商详情"""

    model_config = {'from_attributes': True}

    id: int
    api_key_encrypted: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_api_key(self) -> bool:
        """是否已配置 API Key"""
        return bool(self.api_key_encrypted)

    @classmethod
    def from_orm_with_key_check(cls, obj) -> 'GetProviderDetail':
        return cls.model_validate(obj)


class GetProviderList(SchemaBase):
    """供应商列表项"""

    model_config = {'from_attributes': True}

    id: int
    name: str
    provider_type: str
    api_base_url: str | None = None
    global_rpm_limit: int
    global_tpm_limit: int
    enabled: bool
    is_domestic: bool
    api_key_encrypted: str | None = Field(default=None, exclude=True)
    description: str | None = None

    @computed_field
    @property
    def has_api_key(self) -> bool:
        """是否已配置 API Key"""
        return bool(self.api_key_encrypted)
