from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageMigrationItemsSchemaBase(SchemaBase):
    """用户云存储迁移逐对象明细基础模型"""
    item_id: str = Field(description='迁移明细稳定 ID')
    job_id: str = Field(description='所属迁移作业 ID')
    object_id: str = Field(description='被迁移物理对象 ID')
    source_storage_id: int = Field(description='迁移前存储配置 ID')
    source_object_key: str = Field(description='迁移前对象键')
    source_key_layout: str = Field(description='迁移前对象键布局（精确回滚依据）')
    target_storage_id: int = Field(description='目标存储配置 ID')
    target_object_key: str = Field(description='目标对象键')
    source_size_bytes: int = Field(description='源对象校验字节数')
    source_sha256: str | None = Field(None, description='源对象校验 SHA-256')
    verify_status: str = Field(description='校验状态 (pending:待处理:blue/copied:已复制:orange/verified:已校验:green/switched:已切换:green/rolled_back:已回滚:gray/failed:失败:red)')
    source_cleanup_status: str = Field(description='源对象清理状态 (retained:观察期保留:blue/deleted:已清理:green/shared:跨主人共享保留:orange/failed:清理失败:red)')
    source_deleted_time: datetime | None = Field(None, description='源对象确认删除时间')
    error_code: str | None = Field(None, description='失败错误码')


class CreateHasnStorageMigrationItemsParam(HasnStorageMigrationItemsSchemaBase):
    """创建用户云存储迁移逐对象明细参数"""


class UpdateHasnStorageMigrationItemsParam(HasnStorageMigrationItemsSchemaBase):
    """更新用户云存储迁移逐对象明细参数"""


class DeleteHasnStorageMigrationItemsParam(SchemaBase):
    """删除用户云存储迁移逐对象明细参数"""

    pks: list[int] = Field(description='用户云存储迁移逐对象明细 ID 列表')


class GetHasnStorageMigrationItemsDetail(HasnStorageMigrationItemsSchemaBase):
    """用户云存储迁移逐对象明细详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
