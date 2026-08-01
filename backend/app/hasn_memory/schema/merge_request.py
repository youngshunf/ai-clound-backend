"""合并待办 schema（hasn_memory.merge_request，doc19 §5.5）。"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MergeRequestSchemaBase(SchemaBase):
    """合并待办基础模型（落库参数复用）。"""

    owner_id: str = Field(description='主人 hasn_id（主键：每主人至多一条待办，重复请求覆盖）')
    requested_by_agent: str = Field(description='发起请求的分身 hasn_id（非主脑分身）')
    requested_by_node: str = Field(description='发起请求的节点 node_id')
    reason: str | None = Field(None, description='请求原因（local_review_done / owner_manual 等）')


class CreateMergeRequestParam(MergeRequestSchemaBase):
    """登记（或覆盖）合并待办参数。"""


class GetMergeRequestDetail(MergeRequestSchemaBase):
    """合并待办详情（主键即 owner_id，无 fba 自增 id）。"""

    model_config = ConfigDict(from_attributes=True)

    requested_time: datetime = Field(description='最近一次请求时间（滞留时长 = now - 本值）')
    consumed_time: datetime | None = Field(None, description='被主脑消化的时间（NULL = 待办仍在）')
    created_time: datetime
    updated_time: datetime | None = None
