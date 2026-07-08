from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnGroupAgentInvitesSchemaBase(SchemaBase):
    """HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）基础模型"""
    conversation_id: str | UUID = Field(description='群会话 ID（关联 hasn_conversations）')
    group_id: str = Field(description='群组公开标识（g:NNNNNN）')
    agent_hasn_id: str = Field(description='被邀请的分身 hasn_id')
    agent_owner_id: str = Field(description='分身主人 hasn_id（冗余列，便于按主人查询/判权）')
    inviter_id: str = Field(description='发起人 hasn_id')
    status: str = Field(description='状态 (pending:待确认:orange/accepted:已同意:green/declined:已拒绝:red/expired:已过期:gray/cancelled:已取消:gray)')
    resolved_time: datetime | None = Field(None, description='处理时间（accept/decline/expire/cancel）')


class CreateHasnGroupAgentInvitesParam(HasnGroupAgentInvitesSchemaBase):
    """创建HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）参数"""


class UpdateHasnGroupAgentInvitesParam(HasnGroupAgentInvitesSchemaBase):
    """更新HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）参数"""


class DeleteHasnGroupAgentInvitesParam(SchemaBase):
    """删除HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）参数"""

    pks: list[int] = Field(description='HASN 群内拉分身邀请确认表（非主人拉分身需主人同意） ID 列表')


class GetHasnGroupAgentInvitesDetail(HasnGroupAgentInvitesSchemaBase):
    """HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
