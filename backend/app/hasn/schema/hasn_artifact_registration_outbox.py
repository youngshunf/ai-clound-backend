from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnArtifactRegistrationOutboxSchemaBase(SchemaBase):
    """Agent 产物登记可靠投递与修复队列基础模型"""
    outbox_id: str = Field(description='队列记录公开标识')
    owner_hasn_id: str = Field(description='主人隔离键')
    artifact_id: str | None = Field(None, description='已归一产物公开标识')
    idempotency_key: str = Field(description='登记来源幂等键')
    payload: dict = Field(description='不含正文和本地绝对路径的修复载荷')
    status: str = Field(description='投递状态 (pending:待处理:processing:处理中:completed:已完成:dead_letter:终局失败)')
    attempt_count: int = Field(description='已尝试次数')
    next_retry_at: datetime = Field(description='下次可领取时间')
    lease_until: datetime | None = Field(None, description='处理租约截止时间')
    last_error: str | None = Field(None, description='最近失败诊断')


class CreateHasnArtifactRegistrationOutboxParam(HasnArtifactRegistrationOutboxSchemaBase):
    """创建Agent 产物登记可靠投递与修复队列参数"""


class UpdateHasnArtifactRegistrationOutboxParam(HasnArtifactRegistrationOutboxSchemaBase):
    """更新Agent 产物登记可靠投递与修复队列参数"""


class DeleteHasnArtifactRegistrationOutboxParam(SchemaBase):
    """删除Agent 产物登记可靠投递与修复队列参数"""

    pks: list[int] = Field(description='Agent 产物登记可靠投递与修复队列 ID 列表')


class GetHasnArtifactRegistrationOutboxDetail(HasnArtifactRegistrationOutboxSchemaBase):
    """Agent 产物登记可靠投递与修复队列详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
