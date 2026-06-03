from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnCirclesSchemaBase(SchemaBase):
    """社区圈子实体基础模型"""
    circle_id: str = Field(description='全局唯一 ID，格式 cir_{nanoid}')
    name: str = Field(description='圈子名称')
    slug: str = Field(description='公开路由 /community/circles/{slug}')
    description: str | None = Field(None, description='圈子简介')
    cover_url: str | None = Field(None, description='封面图 URL')
    avatar_url: str | None = Field(None, description='头像 URL')
    owner_hasn_id: str = Field(description='圈主 hasn_id（责任主体，必须为 Human，Agent 不可单独当圈主）')
    origin_workspace_kind: str = Field(description='来源 workspace 类型 (personal:个人/enterprise:企业)')
    origin_workspace_id: str = Field(description='来源 workspace 标识')
    visibility: str = Field(description='可见性 (public:公开圈:green/private:私密圈:gray)')
    join_policy: str = Field(description='加入策略 (open:直接加入:green/approval:申请审批:orange/invite:仅邀请:blue)')
    post_policy: str = Field(description='发帖策略 (members:成员可发:green/approval:发帖需审:orange/owner_admin:仅管理者:red)')
    member_count: int = Field(description='成员数（冗余，异步维护）')
    content_count: int = Field(description='内容数（冗余，异步维护）')
    status: str = Field(description='状态 (active:正常:green/archived:已归档:gray/blocked:已封禁:red)')


class CreateHasnCirclesParam(HasnCirclesSchemaBase):
    """创建社区圈子实体参数"""


class UpdateHasnCirclesParam(HasnCirclesSchemaBase):
    """更新社区圈子实体参数"""


class DeleteHasnCirclesParam(SchemaBase):
    """删除社区圈子实体参数"""

    pks: list[int] = Field(description='社区圈子实体 ID 列表')


class GetHasnCirclesDetail(HasnCirclesSchemaBase):
    """社区圈子实体详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
