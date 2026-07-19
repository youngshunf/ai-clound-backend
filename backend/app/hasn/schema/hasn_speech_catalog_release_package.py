from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSpeechCatalogReleasePackageSchemaBase(SchemaBase):
    """语音 release 平台包与签名元数据快照基础模型"""

    release_id: int = Field(description='所属不可变 catalog release')
    package_id: int = Field(description='引用的内容寻址模型包')
    model_id: str = Field(description='签名 catalog 中的稳定模型标识')
    model_version: str = Field(description='签名 catalog 中的模型版本')
    os: str = Field(description='目标操作系统')
    arch: str = Field(description='目标 CPU 架构')
    acceleration: str = Field(description='目标加速后端')
    installed_size: int = Field(description='签名声明的安装展开字节数')
    license_name: str = Field(description='签名声明的许可证名称')
    license_url: str = Field(description='签名声明的许可证全文 HTTPS URL')
    source_url: str = Field(description='签名声明的权威来源 HTTPS URL')


class CreateHasnSpeechCatalogReleasePackageParam(HasnSpeechCatalogReleasePackageSchemaBase):
    """创建语音 release 平台包与签名元数据快照参数"""


class UpdateHasnSpeechCatalogReleasePackageParam(HasnSpeechCatalogReleasePackageSchemaBase):
    """更新语音 release 平台包与签名元数据快照参数"""


class DeleteHasnSpeechCatalogReleasePackageParam(SchemaBase):
    """删除语音 release 平台包与签名元数据快照参数"""

    pks: list[int] = Field(description='语音 release 平台包与签名元数据快照 ID 列表')


class GetHasnSpeechCatalogReleasePackageDetail(HasnSpeechCatalogReleasePackageSchemaBase):
    """语音 release 平台包与签名元数据快照详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
