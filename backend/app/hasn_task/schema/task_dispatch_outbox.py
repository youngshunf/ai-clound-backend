from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class TaskDispatchOutboxSchemaBase(SchemaBase):
    """中心任务调度器向主人节点可靠投递任务执行帧的事务队列基础模型"""
    command_id: str = Field(description='派发命令公开标识')
    run_id: int = Field(description='同事务创建的任务运行 ID，同时作为单次派发唯一键')
    task_id: int = Field(description='任务定义 ID')
    target_owner_id: str = Field(description='接收任务执行帧的主人 HASN ID')
    method: str = Field(description='HASN 协议方法，固定 hasn.task.exec')
    payload: dict = Field(description='完整任务执行参数 JSON，不包含 HASN 外层信封')
    payload_hash: str = Field(description='规范化目标、方法与载荷的 SHA-256')
    idempotency_key: str = Field(description='由权威 run ID 派生的稳定派发幂等键')
    status: str = Field(description='投递状态：pending/processing/completed/dead_letter')
    attempt_count: int = Field(description='已失败次数')
    next_attempt_at: datetime = Field(description='下次允许领取时间')
    lease_until: datetime | None = Field(None, description='处理中租约截止时间')
    locked_by: str | None = Field(None, description='当前 relay 实例标识')
    last_error: str | None = Field(None, description='最近一次失败诊断')
    completed_at: datetime | None = Field(None, description='已交给实时投递层的时间')


class CreateTaskDispatchOutboxParam(TaskDispatchOutboxSchemaBase):
    """创建中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数"""


class UpdateTaskDispatchOutboxParam(TaskDispatchOutboxSchemaBase):
    """更新中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数"""


class DeleteTaskDispatchOutboxParam(SchemaBase):
    """删除中心任务调度器向主人节点可靠投递任务执行帧的事务队列参数"""

    pks: list[int] = Field(description='中心任务调度器向主人节点可靠投递任务执行帧的事务队列 ID 列表')


class GetTaskDispatchOutboxDetail(TaskDispatchOutboxSchemaBase):
    """中心任务调度器向主人节点可靠投递任务执行帧的事务队列详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
