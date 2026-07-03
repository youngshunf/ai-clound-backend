from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ActivitySchemaBase(SchemaBase):
    """获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）基础模型"""
    customer_id: int = Field(description='None')
    opportunity_id: int | None = Field(None, description='None')
    user_id: int = Field(description='None')
    kind: str = Field(description='类型 (outreach:触达:blue/reply:回复:green/stage_change:阶段变更:orange/task_run:任务:cyan/note:备注:gray/call:电话:purple/meeting:会议:purple/qualify:晋级:blue/close:成交:green)')
    content: str | None = Field(None, description='None')
    actor_kind: str | None = Field(None, description='执行者 (owner:主人:blue/agent:分身:violet)')
    actor_id: str | None = Field(None, description='None')
    ref_table: str | None = Field(None, description='None')
    ref_id: str | None = Field(None, description='None')
    occurred_at: datetime = Field(description='None')


class CreateActivityParam(ActivitySchemaBase):
    """创建获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数"""


class UpdateActivityParam(ActivitySchemaBase):
    """更新获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数"""


class DeleteActivityParam(SchemaBase):
    """删除获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）参数"""

    pks: list[int] = Field(description='获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水） ID 列表')


class GetActivityDetail(ActivitySchemaBase):
    """获客客户活动时间线（触达/回复/阶段变更/任务 run/人工备注统一流水）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
