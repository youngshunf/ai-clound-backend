"""LLM 用量日志表"""

from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class UsageLog(Base):
    """LLM 用量日志表"""

    __tablename__ = 'llm_usage_log'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='用户 ID')
    api_key_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='API Key ID')
    model_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='模型 ID')
    provider_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='供应商 ID')
    request_id: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True, comment='请求 ID')
    model_name: Mapped[str] = mapped_column(sa.String(128), index=True, comment='模型名称')
    input_tokens: Mapped[int] = mapped_column(default=0, comment='输入 tokens')
    output_tokens: Mapped[int] = mapped_column(default=0, comment='输出 tokens')
    total_tokens: Mapped[int] = mapped_column(default=0, index=True, comment='总 tokens')
    input_cost: Mapped[Decimal] = mapped_column(sa.Numeric(10, 6), default=Decimal(0), comment='输入成本 (USD)')
    output_cost: Mapped[Decimal] = mapped_column(sa.Numeric(10, 6), default=Decimal(0), comment='输出成本 (USD)')
    total_cost: Mapped[Decimal] = mapped_column(sa.Numeric(10, 6), default=Decimal(0), comment='总成本 (USD)')
    latency_ms: Mapped[int] = mapped_column(default=0, comment='延迟(毫秒)')
    status: Mapped[str] = mapped_column(sa.String(16), default='SUCCESS', index=True, comment='状态(SUCCESS/ERROR)')
    error_message: Mapped[str | None] = mapped_column(sa.Text, default=None, comment='错误信息')
    is_streaming: Mapped[bool] = mapped_column(default=False, comment='是否流式')
    ip_address: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='IP 地址')
