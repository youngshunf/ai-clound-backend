"""事实删除凭证 schema（hasn_memory.fact_tombstone，doc19 §4.5）。

**绝不承载被删事实的内容**——本 schema 的字段集就是「不留任何内容」的契约边界，
新增字段前先确认它不是被删事实的正文、对象或理由快照。
"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class FactTombstoneSchemaBase(SchemaBase):
    """事实删除凭证基础模型（落库参数复用）。"""

    fact_id: str = Field(description='被物理删除的事实 ID（主键）')
    owner_id: str = Field(description='主人 hasn_id（hasn_humans.hasn_id）')
    purged_by: str = Field(description='发起人 hasn_id（purge 只有主人可发起）')
    cascade_from: str | None = Field(None, description='级联来源 fact_id（因血缘级联被删时指向源事实）')
    reason: str | None = Field(None, description='删除原因（面向主人的说明，不含被删事实内容）')


class CreateFactTombstoneParam(FactTombstoneSchemaBase):
    """登记事实删除凭证参数。"""


class GetFactTombstoneDetail(FactTombstoneSchemaBase):
    """事实删除凭证详情（主键即 fact_id，无 fba 自增 id）。"""

    model_config = ConfigDict(from_attributes=True)

    purged_time: datetime = Field(description='物理删除执行时间')
    created_time: datetime
    updated_time: datetime | None = None
