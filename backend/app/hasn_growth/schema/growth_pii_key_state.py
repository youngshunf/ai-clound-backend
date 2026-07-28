from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthPiiKeyStateSchemaBase(SchemaBase):
    """Growth PII 写入密钥版本单例栅栏基础模型"""

    min_encryption_write_version: int = Field(description='允许写入的最低加密密钥版本')
    min_hmac_write_version: int = Field(description='允许写入的最低 HMAC 密钥版本')


class CreateGrowthPiiKeyStateParam(GrowthPiiKeyStateSchemaBase):
    """创建Growth PII 写入密钥版本单例栅栏参数"""


class UpdateGrowthPiiKeyStateParam(GrowthPiiKeyStateSchemaBase):
    """更新Growth PII 写入密钥版本单例栅栏参数"""


class DeleteGrowthPiiKeyStateParam(SchemaBase):
    """删除Growth PII 写入密钥版本单例栅栏参数"""

    pks: list[int] = Field(description='Growth PII 写入密钥版本单例栅栏 ID 列表')


class GetGrowthPiiKeyStateDetail(GrowthPiiKeyStateSchemaBase):
    """Growth PII 写入密钥版本单例栅栏详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
