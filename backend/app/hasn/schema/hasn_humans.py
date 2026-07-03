from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnHumansSchemaBase(SchemaBase):
    """HASN 人类用户身份基础模型"""
    hasn_id: str = Field(description='HASN 唯一标识 (h_{uuid})')
    star_id: str = Field(description='唤星号 (数字号或自定义号)')
    user_id: int = Field(description='关联唤星平台用户 ID')
    nickname: str = Field(description='昵称（与 sys_user.nickname 对齐）')
    bio: str | None = Field(None, description='个人简介')
    avatar: str | None = Field(None, description='头像（与 sys_user.avatar 对齐）')
    status: str = Field(description='状态 (active:正常:green/suspended:已暂停:orange/deleted:已注销:red)')
    contact_policy: dict = Field(description='联系人策略 (JSONB)')
    timezone: str | None = Field(None, description='时区')
    tags: str | None = Field(None, description='个人标签')
    stats: dict = Field(description='统计信息 (JSONB)')


class CreateHasnHumansParam(HasnHumansSchemaBase):
    """创建HASN 人类用户身份参数"""


class UpdateHasnHumansParam(HasnHumansSchemaBase):
    """更新HASN 人类用户身份参数"""


class DeleteHasnHumansParam(SchemaBase):
    """删除HASN 人类用户身份参数"""

    pks: list[int] = Field(description='HASN 人类用户身份 ID 列表')


class GetHasnHumansDetail(HasnHumansSchemaBase):
    """HASN 人类用户身份详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
