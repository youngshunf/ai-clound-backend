from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProjectPlaybookSchemaBase(SchemaBase):
    """获客漏斗采用的打法版本与项目级配置快照基础模型"""

    growth_project_id: UUID = Field(description='None')
    playbook_id: int = Field(description='None')
    playbook_version: int = Field(description='None')
    status: str = Field(description='None')
    configuration_snapshot: dict = Field(description='None')


class CreateGrowthProjectPlaybookParam(GrowthProjectPlaybookSchemaBase):
    """创建获客漏斗采用的打法版本与项目级配置快照参数"""


class UpdateGrowthProjectPlaybookParam(GrowthProjectPlaybookSchemaBase):
    """更新获客漏斗采用的打法版本与项目级配置快照参数"""


class DeleteGrowthProjectPlaybookParam(SchemaBase):
    """删除获客漏斗采用的打法版本与项目级配置快照参数"""

    pks: list[int] = Field(description='获客漏斗采用的打法版本与项目级配置快照 ID 列表')


class GetGrowthProjectPlaybookDetail(GrowthProjectPlaybookSchemaBase):
    """获客漏斗采用的打法版本与项目级配置快照详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
