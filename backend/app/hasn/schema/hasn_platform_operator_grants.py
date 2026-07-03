from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnPlatformOperatorGrantsSchemaBase(SchemaBase):
    """平台运维授予源（Admin-only·G1 特权门）基础模型"""
    agent_hasn_id: str = Field(description='被授予的分身 hasn_id')
    scope: str = Field(description='特权 scope（精确值 diag:read:all / diag:manage 或段尾通配 ops:*，* 仅限末段）')
    granted_by: str = Field(description='操作的 Admin（审计）')
    note: str | None = Field(None, description='备注（授予理由，可空）')


class CreateHasnPlatformOperatorGrantsParam(HasnPlatformOperatorGrantsSchemaBase):
    """创建平台运维授予源（Admin-only·G1 特权门）参数"""


class UpdateHasnPlatformOperatorGrantsParam(HasnPlatformOperatorGrantsSchemaBase):
    """更新平台运维授予源（Admin-only·G1 特权门）参数"""


class DeleteHasnPlatformOperatorGrantsParam(SchemaBase):
    """删除平台运维授予源（Admin-only·G1 特权门）参数"""

    pks: list[int] = Field(description='平台运维授予源（Admin-only·G1 特权门） ID 列表')


class GetHasnPlatformOperatorGrantsDetail(HasnPlatformOperatorGrantsSchemaBase):
    """平台运维授予源（Admin-only·G1 特权门）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
