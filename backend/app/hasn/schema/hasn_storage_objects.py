from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageObjectsSchemaBase(SchemaBase):
    """用户云存储物理对象基础模型"""
    object_id: str = Field(description='物理对象稳定 ID')
    owner_hasn_id: str | None = Field(None, description='所属主人 hasn_id；平台资产为空')
    storage_id: int = Field(description='对象存储配置 ID')
    object_key: str = Field(description='对象键（相对存储根）')
    key_layout: str = Field(description='对象键布局 (owner_scoped:主人隔离:blue/legacy:存量兼容:orange/platform:平台资产:green)')
    access: str = Field(description='访问类型 (private:私有:orange/public:公开:green)')
    size_bytes: int = Field(description='服务端校准后的权威字节数')
    sha256: str | None = Field(None, description='服务端计算的 SHA-256')
    billable_to_owner: bool = Field(description='是否计入主人配额')
    ref_count: int = Field(description='非删除逻辑资产引用数')
    state: str = Field(description='对象状态 (pending:待确认:orange/active:可用:green/deleting:删除中:orange/deleted:已删除:gray/missing:对象缺失:red/error:异常:red)')


class CreateHasnStorageObjectsParam(HasnStorageObjectsSchemaBase):
    """创建用户云存储物理对象参数"""


class UpdateHasnStorageObjectsParam(HasnStorageObjectsSchemaBase):
    """更新用户云存储物理对象参数"""


class DeleteHasnStorageObjectsParam(SchemaBase):
    """删除用户云存储物理对象参数"""

    pks: list[int] = Field(description='用户云存储物理对象 ID 列表')


class GetHasnStorageObjectsDetail(HasnStorageObjectsSchemaBase):
    """用户云存储物理对象详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
