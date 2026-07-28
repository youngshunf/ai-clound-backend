from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OptoutRecordSchemaBase(SchemaBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）基础模型"""
    user_id: int = Field(description='None')
    owner_scope: str = Field(default='personal', description='退订作用域')
    enterprise_id: int | None = Field(default=None, description='企业作用域 ID')
    channel: str = Field(description='渠道；all=全渠道')
    address_hash: str | None = Field(
        default=None,
        description='旧 SHA256 兼容列，只读，禁止新写',
    )
    address_hmac: str | None = Field(
        default=None,
        description='归一化联系方式的服务端 HMAC',
    )
    hash_key_version: int | None = Field(default=None, description='HMAC 密钥版本')
    customer_id: int | None = Field(None, description='None')
    reason: str | None = Field(None, description='None')
    source: str | None = Field(None, description='None')


class CreateOptoutRecordParam(OptoutRecordSchemaBase):
    """已退役的管理端写模型；服务层始终拒绝，Owner 统一走业务端点。"""


class UpdateOptoutRecordParam(OptoutRecordSchemaBase):
    """已退役的管理端写模型；服务层始终拒绝，退订记录不可改写。"""


class DeleteOptoutRecordParam(SchemaBase):
    """删除获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数"""

    pks: list[int] = Field(
        description='获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID 列表'
    )


class GetOptoutRecordDetail(OptoutRecordSchemaBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
