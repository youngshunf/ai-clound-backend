from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageExportItemsSchemaBase(SchemaBase):
    """用户云存储导出逐资产不可变快照基础模型"""
    item_id: str = Field(description='导出明细稳定 ID')
    job_id: str = Field(description='所属导出作业 ID')
    owner_hasn_id: str = Field(description='所属主人 hasn_id')
    asset_id: str = Field(description='快照中的逻辑资产 ID')
    logical_path: str = Field(description='创建导出时冻结的逻辑路径')
    original_name: str = Field(description='创建导出时冻结的原始文件名')
    mime: str = Field(description='MIME 类型')
    source_app: str | None = Field(None, description='业务来源应用')
    access: str = Field(description='对象访问级别')
    asset_created_time: datetime = Field(description='资产原始创建时间')
    lifecycle_status: str = Field(description='创建导出时的生命周期状态')
    bindings: list[dict[str, str]] = Field(description='创建导出时冻结的业务引用清单')
    object_id: str = Field(description='物理对象 ID')
    storage_id: int = Field(description='快照中的存储配置 ID')
    object_key: str = Field(description='快照中的对象键')
    size_bytes: int = Field(description='预期对象字节数')
    sha256: str | None = Field(None, description='预期对象 SHA-256')
    verify_status: str = Field(description='校验状态 (pending:待校验:blue/verified:已校验:green/failed:失败:red)')
    error_code: str | None = Field(None, description='失败错误码')


class CreateHasnStorageExportItemsParam(HasnStorageExportItemsSchemaBase):
    """创建用户云存储导出逐资产不可变快照参数"""


class UpdateHasnStorageExportItemsParam(HasnStorageExportItemsSchemaBase):
    """更新用户云存储导出逐资产不可变快照参数"""


class DeleteHasnStorageExportItemsParam(SchemaBase):
    """删除用户云存储导出逐资产不可变快照参数"""

    pks: list[int] = Field(description='用户云存储导出逐资产不可变快照 ID 列表')


class GetHasnStorageExportItemsDetail(HasnStorageExportItemsSchemaBase):
    """用户云存储导出逐资产不可变快照详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
