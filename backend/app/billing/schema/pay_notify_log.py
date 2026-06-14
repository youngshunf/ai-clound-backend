from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GetPayNotifyLogDetail(SchemaBase):
    """回调日志详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str | None = None
    channel_code: str | None = None
    notify_type: str
    notify_data: str | None = None
    status: int
    error_msg: str | None = None
    created_time: datetime
