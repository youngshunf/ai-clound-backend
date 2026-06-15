"""用户 API Key 表"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class UserApiKey(Base):
    """用户 API Key 表"""

    __tablename__ = 'llm_user_api_key'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='用户 ID')
    name: Mapped[str] = mapped_column(sa.String(64), comment='Key 名称')
    key_prefix: Mapped[str] = mapped_column(sa.String(16), index=True, comment='Key 前缀(sk-xxx)')
    key_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True, comment='SHA-256 哈希')
    key_encrypted: Mapped[str] = mapped_column(sa.Text, comment='AES-256 加密的完整 Key')
    status: Mapped[str] = mapped_column(sa.String(16), default='ACTIVE', index=True, comment='状态')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间')
    rate_limit_config_id: Mapped[int | None] = mapped_column(sa.BigInteger, default=None, comment='速率限制配置 ID')
    custom_daily_tokens: Mapped[int | None] = mapped_column(default=None, comment='自定义日 Token 限制')
    custom_monthly_tokens: Mapped[int | None] = mapped_column(default=None, comment='自定义月 Token 限制')
    custom_rpm_limit: Mapped[int | None] = mapped_column(default=None, comment='自定义 RPM 限制')
    allowed_models: Mapped[list | None] = mapped_column(sa.JSON, default=None, comment='允许的模型列表')
    metadata_: Mapped[dict | None] = mapped_column('metadata', sa.JSON, default=None, comment='元数据')
    last_used_at: Mapped[datetime | None] = mapped_column(TimeZone, init=False, default=None, comment='最后使用时间')
