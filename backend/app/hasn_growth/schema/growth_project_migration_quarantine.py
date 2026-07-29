from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProjectMigrationQuarantineSchemaBase(SchemaBase):
    """获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单基础模型"""

    source_table: str = Field(description='异常来源表')
    source_record_id: str = Field(description='来源记录稳定 ID')
    reason_code: str = Field(description='可监控的异常原因码')
    owner_scope_hint: str | None = Field(None, description='待核实的归属范围')
    user_id_hint: int | None = Field(None, description='待核实的 Owner 用户 ID')
    enterprise_id_hint: int | None = Field(None, description='待核实的企业 ID')
    details: dict = Field(description='只允许原因分类、状态名和稳定资源键，禁止保存联系人明文')
    status: str = Field(description='处理状态：pending、resolved 或 ignored')
    resolution_note: str | None = Field(None, description='处理结论')
    resolved_by: str | None = Field(None, description='处理人稳定身份')
    resolved_time: datetime | None = Field(None, description='处理完成时间')


class CreateGrowthProjectMigrationQuarantineParam(GrowthProjectMigrationQuarantineSchemaBase):
    """创建获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单参数"""


class UpdateGrowthProjectMigrationQuarantineParam(GrowthProjectMigrationQuarantineSchemaBase):
    """更新获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单参数"""


class DeleteGrowthProjectMigrationQuarantineParam(SchemaBase):
    """删除获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单参数"""

    pks: list[int] = Field(description='获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单 ID 列表')


class GetGrowthProjectMigrationQuarantineDetail(GrowthProjectMigrationQuarantineSchemaBase):
    """获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
