from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAgentCapabilitiesSchemaBase(SchemaBase):
    """HASN Agent 能力声明基础模型"""
    agent_hasn_id: str = Field(description='Agent 的 hasn_id')
    capability_id: str = Field(description='能力唯一标识')
    name: str = Field(description='能力名称')
    description: str | None = Field(None, description='能力描述')
    input_schema: dict = Field(description='输入 JSON Schema')
    output_schema: dict = Field(description='输出 JSON Schema')
    requires_permission: dict = Field(description='所需权限（JSONB: relation_types/min_trust_level/scopes）')
    tags: str | None = Field(None, description='能力标签')
    estimated_time_ms: int = Field(description='预计耗时（毫秒）')
    idempotent: bool = Field(description='是否幂等')
    status: str = Field(description='状态 (active:启用:green/disabled:已禁用:orange)')


class CreateHasnAgentCapabilitiesParam(HasnAgentCapabilitiesSchemaBase):
    """创建HASN Agent 能力声明参数"""


class UpdateHasnAgentCapabilitiesParam(HasnAgentCapabilitiesSchemaBase):
    """更新HASN Agent 能力声明参数"""


class DeleteHasnAgentCapabilitiesParam(SchemaBase):
    """删除HASN Agent 能力声明参数"""

    pks: list[int] = Field(description='HASN Agent 能力声明 ID 列表')


class GetHasnAgentCapabilitiesDetail(HasnAgentCapabilitiesSchemaBase):
    """HASN Agent 能力声明详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
