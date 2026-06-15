from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PayAppSchemaBase(SchemaBase):
    """支付应用基础模型"""
    app_key: str = Field(description='应用标识（如 huanxing）')
    name: str = Field(description='应用名称')
    status: int = Field(1, description='状态 1=启用 0=停用')
    remark: str | None = Field(None, description='备注')
    order_notify_url: str = Field(description='支付成功回调地址')
    refund_notify_url: str | None = Field(None, description='退款回调地址')


class CreatePayAppParam(PayAppSchemaBase):
    """创建支付应用参数"""


class UpdatePayAppParam(PayAppSchemaBase):
    """更新支付应用参数"""


class DeletePayAppParam(SchemaBase):
    """删除支付应用参数"""
    pks: list[int] = Field(description='支付应用 ID 列表')


class GetPayAppDetail(PayAppSchemaBase):
    """支付应用详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
