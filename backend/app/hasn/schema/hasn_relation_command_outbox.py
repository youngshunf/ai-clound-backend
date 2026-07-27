from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnRelationCommandOutboxSchemaBase(SchemaBase):
    """身份事实投影为 IM 关系的可靠命令队列基础模型"""
    command_id: str = Field(description='命令公开标识')
    command_type: str = Field(description='关系命令类型')
    owner_hasn_id: str = Field(description='控制边主人 HASN ID')
    peer_hasn_id: str = Field(description='主人名下分身 HASN ID')
    idempotency_key: str = Field(description='跨重试稳定幂等键')
    status: str = Field(description='投递状态：pending/processing/completed/dead_letter')
    attempt_count: int = Field(description='已失败次数')
    next_retry_at: datetime = Field(description='下次允许领取时间')
    lease_until: datetime | None = Field(None, description='处理租约截止时间')
    last_error: str | None = Field(None, description='最近一次失败诊断')
    completed_at: datetime | None = Field(None, description='投递完成时间')


class CreateHasnRelationCommandOutboxParam(HasnRelationCommandOutboxSchemaBase):
    """创建身份事实投影为 IM 关系的可靠命令队列参数"""


class UpdateHasnRelationCommandOutboxParam(HasnRelationCommandOutboxSchemaBase):
    """更新身份事实投影为 IM 关系的可靠命令队列参数"""


class DeleteHasnRelationCommandOutboxParam(SchemaBase):
    """删除身份事实投影为 IM 关系的可靠命令队列参数"""

    pks: list[int] = Field(description='身份事实投影为 IM 关系的可靠命令队列 ID 列表')


class GetHasnRelationCommandOutboxDetail(HasnRelationCommandOutboxSchemaBase):
    """身份事实投影为 IM 关系的可靠命令队列详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
