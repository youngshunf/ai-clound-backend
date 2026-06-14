from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GetPayRefundDetail(SchemaBase):
    """退款记录详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    refund_no: str
    order_no: str
    user_id: int
    channel_code: str | None = None
    refund_amount: int
    reason: str | None = None
    channel_refund_no: str | None = None
    status: int
    success_time: datetime | None = None
    created_time: datetime
    updated_time: datetime | None = None
