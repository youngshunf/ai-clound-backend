from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

ScopeLifecycleState = Literal["pending", "active", "closed", "expired"]


class HasnTradeSessionsSchemaBase(SchemaBase):
    """HASN 交易会话基础模型"""
    buyer_id: str = Field(description='买方 hasn_id')
    seller_id: str = Field(description='卖方 hasn_id')
    relation_type: str = Field(description='关系类型 (commerce:商业:orange/service:履约:green/professional:专业:purple/platform:平台:cyan)')
    scope: str = Field(description='当前作用域 (commerce: pre_sale:售前/negotiation:协商/in_order:订单中/fulfilling:履约中/after_sale:售后/subscription:订阅 | service: active_order:活跃订单 | professional: consultation:咨询/treatment:进行中/follow_up:跟进 | platform: app_installation:应用安装/system_notice:系统通知)')
    status: str = Field(description='状态 (active:进行中:green/completed:已完成:blue/archived:已归档:gray/cancelled:已取消:red)')
    lifecycle_state: ScopeLifecycleState = Field(default='active', description='作用域生命周期 (pending:待激活:gray/active:激活:violet/closed:已关闭:neutral/expired:已过期:red)')
    order_id: str | None = Field(None, description='关联订单 ID')
    expires_at: datetime | None = Field(None, description='过期时间')
    meta_data: dict = Field(description='附加元数据 (JSONB)')


class CreateHasnTradeSessionsParam(HasnTradeSessionsSchemaBase):
    """创建HASN 交易会话参数"""


class UpdateHasnTradeSessionsParam(HasnTradeSessionsSchemaBase):
    """更新HASN 交易会话参数"""


class DeleteHasnTradeSessionsParam(SchemaBase):
    """删除HASN 交易会话参数"""

    pks: list[int] = Field(description='HASN 交易会话 ID 列表')


class GetHasnTradeSessionsDetail(HasnTradeSessionsSchemaBase):
    """HASN 交易会话详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
