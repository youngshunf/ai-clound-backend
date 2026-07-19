from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSpeechCatalogReleaseSchemaBase(SchemaBase):
    """语音签名 catalog 不可变发布历史基础模型"""

    revision: str = Field(description='catalog 原文 SHA-256 前 16 位')
    release_sequence: Decimal = Field(description='全目录单调 u64 发布序列')
    key_id: str = Field(description='签名信任环中的稳定公钥标识')
    catalog_version: str = Field(description='签名正文中的目录版本')
    expires_at: datetime = Field(description='发布信封失效时间')
    catalog_json: str = Field(description='离线签名 catalog 逐字节原文')
    model_summary: dict = Field(description='管理展示用模型摘要，非验签权威')
    published_by: str | None = Field(None, description='发布方审计标识')


class CreateHasnSpeechCatalogReleaseParam(HasnSpeechCatalogReleaseSchemaBase):
    """创建语音签名 catalog 不可变发布历史参数"""


class UpdateHasnSpeechCatalogReleaseParam(HasnSpeechCatalogReleaseSchemaBase):
    """更新语音签名 catalog 不可变发布历史参数"""


class DeleteHasnSpeechCatalogReleaseParam(SchemaBase):
    """删除语音签名 catalog 不可变发布历史参数"""

    pks: list[int] = Field(description='语音签名 catalog 不可变发布历史 ID 列表')


class GetHasnSpeechCatalogReleaseDetail(HasnSpeechCatalogReleaseSchemaBase):
    """语音签名 catalog 不可变发布历史详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
