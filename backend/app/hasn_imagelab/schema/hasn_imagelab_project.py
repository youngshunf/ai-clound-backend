from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnImagelabProjectDetail(SchemaBase):
    """图坊项目云端轻登记详情（云端权威 ID 源，模块 14 doc30 §5.9 B1）"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description='云端权威 ID（server_id）')
    owner_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    local_ref: str = Field(description='daemon 本地项目 ULID（本地权威 ID）')
    name: str = Field(description='项目名')
    created_time: datetime
    updated_time: datetime | None = None
