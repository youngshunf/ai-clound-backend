from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProjectProvisionSchemaBase(SchemaBase):
    """建漏斗、建库、挂靠和建站步骤的可靠编排状态基础模型"""

    growth_project_id: UUID = Field(description='None')
    command_id: str = Field(description='None')
    idempotency_key: str = Field(description='None')
    step: str = Field(description='None')
    status: str = Field(description='None')
    attempts: int = Field(description='None')
    next_retry_time: datetime | None = Field(None, description='None')
    last_error: dict | None = Field(None, description='None')
    started_time: datetime | None = Field(None, description='None')
    finished_time: datetime | None = Field(None, description='None')


class CreateGrowthProjectProvisionParam(GrowthProjectProvisionSchemaBase):
    """创建建漏斗、建库、挂靠和建站步骤的可靠编排状态参数"""


class UpdateGrowthProjectProvisionParam(GrowthProjectProvisionSchemaBase):
    """更新建漏斗、建库、挂靠和建站步骤的可靠编排状态参数"""


class DeleteGrowthProjectProvisionParam(SchemaBase):
    """删除建漏斗、建库、挂靠和建站步骤的可靠编排状态参数"""

    pks: list[int] = Field(description='建漏斗、建库、挂靠和建站步骤的可靠编排状态 ID 列表')


class GetGrowthProjectProvisionDetail(GrowthProjectProvisionSchemaBase):
    """建漏斗、建库、挂靠和建站步骤的可靠编排状态详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
