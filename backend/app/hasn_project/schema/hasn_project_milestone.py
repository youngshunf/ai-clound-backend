from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnProjectMilestoneSchemaBase(SchemaBase):
    """平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）基础模型"""
    project_id: str | UUID = Field(description='所属项目 id（hasn_project.id，物理 FK 级联删）')
    name: str = Field(description='里程碑名')
    due_time: datetime | None = Field(None, description='到期时间（可空；逾期由读时按当前时间派生，不落库状态）')
    status: str = Field(description='状态 (pending:待完成:blue/done:已完成:green)')
    artifact_ref: str | None = Field(None, description='关联产物引用（hasn:// 资源或 artifact_id，可空；业务交付节点的锚，doc38 §12.4）')
    sort: int = Field(description='排序（里程碑轨横向次序）')


class CreateHasnProjectMilestoneParam(HasnProjectMilestoneSchemaBase):
    """创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数"""


class UpdateHasnProjectMilestoneParam(HasnProjectMilestoneSchemaBase):
    """更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数"""


class DeleteHasnProjectMilestoneParam(SchemaBase):
    """删除平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数"""

    pks: list[int] = Field(description='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID 列表')


class GetHasnProjectMilestoneDetail(HasnProjectMilestoneSchemaBase):
    """平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
