from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PayChannelSchemaBase(SchemaBase):
    """支付渠道基础模型"""
    merchant_id: int | None = Field(None, description='关联商户 ID')
    code: str = Field(description='渠道编码 wx_native/alipay_pc 等')
    name: str = Field(description='渠道显示名称')
    status: int = Field(1, description='状态 1=启用 0=停用')
    fee_rate: Decimal = Field(Decimal('0'), description='费率')
    remark: str | None = Field(None, description='备注')
    extra_config: dict | None = Field(None, description='渠道特有配置')


class CreatePayChannelParam(PayChannelSchemaBase):
    """创建支付渠道参数"""


class UpdatePayChannelParam(PayChannelSchemaBase):
    """更新支付渠道参数"""


class UpdatePayChannelStatusParam(SchemaBase):
    """更新支付渠道状态"""
    status: int = Field(description='状态 1=启用 0=停用')


class DeletePayChannelParam(SchemaBase):
    """删除支付渠道参数"""
    pks: list[int] = Field(description='支付渠道 ID 列表')


class GetPayChannelDetail(PayChannelSchemaBase):
    """支付渠道详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class GetPayChannelSimple(SchemaBase):
    """支付渠道简要信息（用户端展示）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    merchant_id: int | None = None
