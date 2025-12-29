"""模型配置表"""

from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.common.model import Base, id_key

if TYPE_CHECKING:
    from .provider import ModelProvider


class ModelConfig(Base):
    """模型配置表"""

    __tablename__ = 'llm_model_config'

    id: Mapped[id_key] = mapped_column(init=False)
    # === 必填字段（无默认值）===
    provider_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey('llm_model_provider.id'), index=True, comment='供应商 ID')
    model_name: Mapped[str] = mapped_column(sa.String(128), index=True, comment='模型名称')
    model_type: Mapped[str] = mapped_column(sa.String(32), index=True, comment='模型类型')
    # === 可选字段（有默认值）===
    display_name: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='显示名称')
    max_tokens: Mapped[int] = mapped_column(default=4096, comment='最大输出 tokens')
    max_context_length: Mapped[int] = mapped_column(default=8192, comment='最大上下文长度')
    supports_streaming: Mapped[bool] = mapped_column(default=True, comment='支持流式')
    supports_tools: Mapped[bool] = mapped_column(default=False, comment='支持工具调用')
    supports_vision: Mapped[bool] = mapped_column(default=False, comment='支持视觉')
    input_cost_per_1k: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 6), default=Decimal('0'), comment='输入成本/1K tokens (USD)'
    )
    output_cost_per_1k: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 6), default=Decimal('0'), comment='输出成本/1K tokens (USD)'
    )
    rpm_limit: Mapped[int | None] = mapped_column(default=None, comment='模型 RPM 限制')
    tpm_limit: Mapped[int | None] = mapped_column(default=None, comment='模型 TPM 限制')
    priority: Mapped[int] = mapped_column(default=0, comment='优先级(越大越优先)')
    enabled: Mapped[bool] = mapped_column(default=True, index=True, comment='是否启用')

    # 关系
    provider: Mapped['ModelProvider'] = relationship(init=False, lazy='selectin')
