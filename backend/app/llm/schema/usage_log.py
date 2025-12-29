"""用量日志 Schema"""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from backend.common.schema import SchemaBase


class UsageLogBase(SchemaBase):
    """用量日志基础 Schema"""

    user_id: int
    api_key_id: int
    model_id: int
    provider_id: int
    request_id: str
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost: Decimal = Decimal('0')
    output_cost: Decimal = Decimal('0')
    total_cost: Decimal = Decimal('0')
    latency_ms: int = 0
    status: str
    error_message: str | None = None
    is_streaming: bool = False
    ip_address: str | None = None


class GetUsageLogDetail(UsageLogBase):
    """用量日志详情"""

    id: int
    created_time: datetime


class GetUsageLogList(SchemaBase):
    """用量日志列表项"""

    model_config = {'from_attributes': True}

    id: int
    model_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_cost: Decimal
    latency_ms: int
    status: str
    is_streaming: bool
    created_time: datetime


class UsageSummary(SchemaBase):
    """用量汇总"""

    total_requests: int = Field(description='总请求数')
    success_requests: int = Field(description='成功请求数')
    error_requests: int = Field(description='失败请求数')
    total_tokens: int = Field(description='总 tokens')
    total_input_tokens: int = Field(description='总输入 tokens')
    total_output_tokens: int = Field(description='总输出 tokens')
    total_cost: Decimal = Field(description='总成本 (USD)')
    avg_latency_ms: int = Field(description='平均延迟 (ms)')


class DailyUsage(SchemaBase):
    """每日用量"""

    date: str = Field(description='日期 (YYYY-MM-DD)')
    requests: int = Field(description='请求数')
    tokens: int = Field(description='tokens')
    cost: Decimal = Field(description='成本 (USD)')


class ModelUsage(SchemaBase):
    """模型用量"""

    model_name: str
    requests: int
    tokens: int
    cost: Decimal


class QuotaInfo(SchemaBase):
    """配额信息"""

    daily_token_limit: int = Field(description='日 Token 限制')
    daily_token_used: int = Field(description='日 Token 已用')
    daily_token_remaining: int = Field(description='日 Token 剩余')
    monthly_token_limit: int = Field(description='月 Token 限制')
    monthly_token_used: int = Field(description='月 Token 已用')
    monthly_token_remaining: int = Field(description='月 Token 剩余')
    rpm_limit: int = Field(description='RPM 限制')
    current_rpm: int = Field(description='当前 RPM')
