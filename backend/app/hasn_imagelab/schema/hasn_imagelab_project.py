from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnImagelabProjectDetail(SchemaBase):
    """历史图坊本地引用兼容登记详情。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description='历史兼容 server_id')
    owner_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    local_ref: str = Field(description='daemon 历史本地引用')
    name: str = Field(description='历史显示名')
    created_time: datetime
    updated_time: datetime | None = None
