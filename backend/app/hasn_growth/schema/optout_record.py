from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OptoutRecordSchemaBase(SchemaBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）基础模型"""
    user_id: int = Field(description='None')
    channel: str = Field(description='渠道；all=全渠道')
    address_hash: str = Field(description='sha256(归一化联系方式)——不存明文')
    customer_id: int | None = Field(None, description='None')
    reason: str | None = Field(None, description='None')
    source: str | None = Field(None, description='None')


class CreateOptoutRecordParam(OptoutRecordSchemaBase):
    """创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数"""


class UpdateOptoutRecordParam(OptoutRecordSchemaBase):
    """更新获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数"""


class DeleteOptoutRecordParam(SchemaBase):
    """删除获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数"""

    pks: list[int] = Field(description='获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID 列表')


class GetOptoutRecordDetail(OptoutRecordSchemaBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
