from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnCircleMembersSchemaBase(SchemaBase):
    """圈子成员与角色基础模型"""
    circle_id: str = Field(description='所属圈子 circle_id')
    member_hasn_id: str = Field(description='成员 hasn_id（Human 或 Agent）')
    member_type: str = Field(description='成员类型 (human:人类/agent:分身)')
    owner_hasn_id: str = Field(description='成员为 agent 时其主人 hasn_id；human 时=自身')
    role: str = Field(description='角色 (owner:圈主:purple/admin:管理员:blue/member:成员:gray)')
    status: str = Field(description='状态 (active:正常:green/pending:待审批:orange/banned:已封禁:red/left:已退出:gray)')
    invited_by_hasn_id: str | None = Field(None, description='邀请人 hasn_id（invite 流程）')
    joined_time: datetime | None = Field(None, description='加入时间')


class CreateHasnCircleMembersParam(HasnCircleMembersSchemaBase):
    """创建圈子成员与角色参数"""


class UpdateHasnCircleMembersParam(HasnCircleMembersSchemaBase):
    """更新圈子成员与角色参数"""


class DeleteHasnCircleMembersParam(SchemaBase):
    """删除圈子成员与角色参数"""

    pks: list[int] = Field(description='圈子成员与角色 ID 列表')


class GetHasnCircleMembersDetail(HasnCircleMembersSchemaBase):
    """圈子成员与角色详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
