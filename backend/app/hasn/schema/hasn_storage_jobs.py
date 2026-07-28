from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageJobsSchemaBase(SchemaBase):
    """用户云存储持久作业与补偿 outbox基础模型"""
    job_id: str = Field(description='作业稳定 ID')
    owner_hasn_id: str | None = Field(None, description='所属主人 hasn_id；平台对账可为空')
    job_type: str = Field(description='作业类型 (storage_export:导出:blue/storage_migration:迁移:orange/storage_reconcile:对账:green/object_purge:对象清理:red/orphan_cleanup:孤儿清理:orange/unbound_asset_sweep:无引用资产清扫:blue/multipart_abort_sweep:分片清理:purple)')
    status: str = Field(description='作业状态 (pending:待执行:blue/running:执行中:orange/retrying:重试中:orange/paused:已暂停:gray/succeeded:成功:green/failed:失败:red/cancelled:已取消:gray)')
    cursor: dict = Field(description='数据库权威游标')
    total_items: int = Field(description='总条目数')
    processed_items: int = Field(description='已处理条目数')
    failed_items: int = Field(description='失败条目数')
    error_code: str | None = Field(None, description='稳定错误码')
    payload: dict = Field(description='作业输入与补偿数据')
    result: dict = Field(description='作业结果摘要')
    attempt_count: int = Field(description='已执行次数')
    next_attempt_time: datetime | None = Field(None, description='下次重试时间')
    expires_time: datetime | None = Field(None, description='作业或产物过期时间')


class CreateHasnStorageJobsParam(HasnStorageJobsSchemaBase):
    """创建用户云存储持久作业与补偿 outbox参数"""


class UpdateHasnStorageJobsParam(HasnStorageJobsSchemaBase):
    """更新用户云存储持久作业与补偿 outbox参数"""


class DeleteHasnStorageJobsParam(SchemaBase):
    """删除用户云存储持久作业与补偿 outbox参数"""

    pks: list[int] = Field(description='用户云存储持久作业与补偿 outbox ID 列表')


class GetHasnStorageJobsDetail(HasnStorageJobsSchemaBase):
    """用户云存储持久作业与补偿 outbox详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
