"""速率限制配置表"""

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class RateLimitConfig(Base):
    """速率限制配置表"""

    __tablename__ = 'llm_rate_limit_config'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True, comment='配置名称')
    daily_token_limit: Mapped[int] = mapped_column(default=1000000, comment='日 Token 限制')
    weekly_token_limit: Mapped[int | None] = mapped_column(default=None, comment='周 Token 限制')
    monthly_token_limit: Mapped[int] = mapped_column(default=10000000, comment='月 Token 限制')
    rpm_limit: Mapped[int] = mapped_column(default=60, comment='RPM 限制')
    tpm_limit: Mapped[int] = mapped_column(default=100000, comment='TPM 限制')
    enabled: Mapped[bool] = mapped_column(default=True, index=True, comment='是否启用')
    description: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='描述')
