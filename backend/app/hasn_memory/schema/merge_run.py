"""合并轮次 schema（hasn_memory.merge_run，doc19 §5.5 / §5.6）。"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MergeRunSchemaBase(SchemaBase):
    """合并轮次基础模型（落库参数复用）。"""

    run_id: str = Field(description='合并轮次 ID（主键；semantic_fact.merge_verdict_run 指向它）')
    owner_id: str = Field(description='主人 hasn_id（一轮合并只针对一个主人）')
    submitted_node_id: str = Field(description='提交节点 node_id（主脑所在设备）')
    submitted_agent_id: str = Field(description='提交分身 hasn_id（主脑分身）')
    base_owner_memory_version: int = Field(0, description='提交声明的基线 owner_memory.version（合并闸 CAS 依据）')
    status: str = Field('applied', description='轮次结果 (applied/rejected)')
    reject_reason: str | None = Field(None, description='拒绝原因（status=rejected 时必填）')
    facts_judged: int = Field(0, description='本轮读入裁决的活跃事实数')
    facts_merged: int = Field(0, description='本轮标 merged_into 的事实数')
    facts_disputed: int = Field(0, description='本轮标 disputed（待主人确认）的事实数')
    summary: str | None = Field(None, description='主脑用人话写的结果摘要（面向主人，记忆页可见）')


class CreateMergeRunParam(MergeRunSchemaBase):
    """登记合并轮次参数。"""


class GetMergeRunDetail(MergeRunSchemaBase):
    """合并轮次详情（主键即 run_id，无 fba 自增 id）。"""

    model_config = ConfigDict(from_attributes=True)

    started_time: datetime = Field(description='本轮开始时间')
    finished_time: datetime | None = Field(None, description='本轮结束时间（含被拒）')
    created_time: datetime
    updated_time: datetime | None = None
