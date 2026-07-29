from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthPiiMigrationQuarantineSchemaBase(SchemaBase):
    """无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文基础模型"""

    source_table: str = Field(description='None')
    source_record_id: str = Field(description='None')
    reason_code: str = Field(description='None')
    owner_scope_hint: str | None = Field(None, description='None')
    user_id_hint: int | None = Field(None, description='None')
    enterprise_id_hint: int | None = Field(None, description='None')
    field_names: list[str] = Field(description='仅记录出现 PII 的字段名，不记录原值')
    pii_fingerprint: str | None = Field(None, description='用于重跑去重的带密钥指纹，不是明文或无盐哈希')
    status: str = Field(description='None')
    resolution_note: str | None = Field(None, description='None')
    resolved_by: str | None = Field(None, description='None')
    resolved_time: datetime | None = Field(None, description='None')


class CreateGrowthPiiMigrationQuarantineParam(GrowthPiiMigrationQuarantineSchemaBase):
    """创建无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文参数"""


class UpdateGrowthPiiMigrationQuarantineParam(GrowthPiiMigrationQuarantineSchemaBase):
    """更新无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文参数"""


class DeleteGrowthPiiMigrationQuarantineParam(SchemaBase):
    """删除无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文参数"""

    pks: list[int] = Field(description='无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文 ID 列表')


class GetGrowthPiiMigrationQuarantineDetail(GrowthPiiMigrationQuarantineSchemaBase):
    """无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
