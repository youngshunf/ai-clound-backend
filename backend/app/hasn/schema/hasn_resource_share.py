from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnResourceShareSchemaBase(SchemaBase):
    """通用产物共享表（平台级显式协作授权）基础模型"""
    resource_type: str = Field(description='产物类型 (deck:演示文稿:blue/doc:文档:cyan/knowledge:知识库:purple)')
    resource_id: str = Field(description='产物主键（deck 为 bigint 文本）')
    owner_hasn_id: str = Field(description='产物归属者 hasn_id（冗余，便于「我共享出去的」反查）')
    grantee_type: str = Field(description='被授权对象类型 (human:人:blue/agent:分身:purple/enterprise:企业:cyan/role:角色:orange/link:链接:gray)')
    grantee_id: str | None = Field(None, description='被授权对象 ID（human/agent=hasn_id；enterprise=enterprise_id；role=builtin:xxx 或 role.id；link=null）')
    permission: str = Field(description='权限档 (viewer:查看:gray/editor:编辑:blue/manager:管理:green)')
    granted_by: str = Field(description='授权操作者 hasn_id')
    status: str = Field(description='授权状态 (active:生效:green/revoked:已撤销:red)')


class CreateHasnResourceShareParam(HasnResourceShareSchemaBase):
    """创建通用产物共享表（平台级显式协作授权）参数"""


class UpdateHasnResourceShareParam(HasnResourceShareSchemaBase):
    """更新通用产物共享表（平台级显式协作授权）参数"""


class DeleteHasnResourceShareParam(SchemaBase):
    """删除通用产物共享表（平台级显式协作授权）参数"""

    pks: list[int] = Field(description='通用产物共享表（平台级显式协作授权） ID 列表')


class GetHasnResourceShareDetail(HasnResourceShareSchemaBase):
    """通用产物共享表（平台级显式协作授权）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
