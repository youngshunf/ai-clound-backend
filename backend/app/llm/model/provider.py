"""模型供应商表"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class ModelProvider(Base):
    """模型供应商表"""

    __tablename__ = 'llm_model_provider'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True, comment='供应商名称（自定义）')
    provider_type: Mapped[str] = mapped_column(sa.String(32), index=True, comment='供应商类型（决定API格式）')
    api_base_url: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='API 基础 URL')
    api_key_encrypted: Mapped[str | None] = mapped_column(sa.Text, default=None, comment='AES-256 加密的 API Key')
    global_rpm_limit: Mapped[int] = mapped_column(default=60, comment='全局 RPM 限制')
    global_tpm_limit: Mapped[int] = mapped_column(default=100000, comment='全局 TPM 限制')
    enabled: Mapped[bool] = mapped_column(default=True, index=True, comment='是否启用')
    is_domestic: Mapped[bool] = mapped_column(default=False, comment='是否国内供应商')
    description: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='描述')
