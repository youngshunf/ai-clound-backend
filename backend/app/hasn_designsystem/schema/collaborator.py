from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CollaboratorSchemaBase(SchemaBase):
    """设计系统协作分身绑定（对齐 DECKBIND）基础模型"""
    design_system_id: int = Field(description='所属 design_system.id')
    agent_hasn_id: str = Field(description='协作分身 HASN ID（a_* 分身）')
    added_by: str = Field(description='添加者 HASN ID（owner）')


class CreateCollaboratorParam(CollaboratorSchemaBase):
    """创建设计系统协作分身绑定（对齐 DECKBIND）参数"""


class UpdateCollaboratorParam(CollaboratorSchemaBase):
    """更新设计系统协作分身绑定（对齐 DECKBIND）参数"""


class DeleteCollaboratorParam(SchemaBase):
    """删除设计系统协作分身绑定（对齐 DECKBIND）参数"""

    pks: list[int] = Field(description='设计系统协作分身绑定（对齐 DECKBIND） ID 列表')


class GetCollaboratorDetail(CollaboratorSchemaBase):
    """设计系统协作分身绑定（对齐 DECKBIND）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
