from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSpeechPackageSchemaBase(SchemaBase):
    """语音模型不可变内容寻址包登记基础模型"""

    sha256: str = Field(description='上传原始字节的规范小写 SHA-256，全局唯一')
    storage_id: int = Field(description='实际承载对象的公共 S3 存储 ID')
    object_key: str = Field(description='由 SHA-256 派生的不可变对象 key')
    size: int = Field(description='对象字节数')
    content_type: str = Field(description='对象媒体类型，模型包固定为 application/zip')


class CreateHasnSpeechPackageParam(HasnSpeechPackageSchemaBase):
    """创建语音模型不可变内容寻址包登记参数"""


class UpdateHasnSpeechPackageParam(HasnSpeechPackageSchemaBase):
    """更新语音模型不可变内容寻址包登记参数"""


class DeleteHasnSpeechPackageParam(SchemaBase):
    """删除语音模型不可变内容寻址包登记参数"""

    pks: list[int] = Field(description='语音模型不可变内容寻址包登记 ID 列表')


class GetHasnSpeechPackageDetail(HasnSpeechPackageSchemaBase):
    """语音模型不可变内容寻址包登记详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
