from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSyncBusinessReceiptsSchemaBase(SchemaBase):
    """sync inbox 业务应用的事务内幂等回执基础模型"""
    idempotency_key: str = Field(description='worker 派生的稳定幂等键')
    owner_id: str = Field(description='主人隔离键')
    node_id: str = Field(description='上报节点 ID')
    client_event_id: str = Field(description='客户端事件 ID')
    event_type: str = Field(description='已应用的业务事件类型')
    applied_at: datetime = Field(description='业务事务提交时间')


class CreateHasnSyncBusinessReceiptsParam(HasnSyncBusinessReceiptsSchemaBase):
    """创建sync inbox 业务应用的事务内幂等回执参数"""


class UpdateHasnSyncBusinessReceiptsParam(HasnSyncBusinessReceiptsSchemaBase):
    """更新sync inbox 业务应用的事务内幂等回执参数"""


class DeleteHasnSyncBusinessReceiptsParam(SchemaBase):
    """删除sync inbox 业务应用的事务内幂等回执参数"""

    pks: list[int] = Field(description='sync inbox 业务应用的事务内幂等回执 ID 列表')


class GetHasnSyncBusinessReceiptsDetail(HasnSyncBusinessReceiptsSchemaBase):
    """sync inbox 业务应用的事务内幂等回执详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
