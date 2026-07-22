from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class HasnSpeechCatalogRelease(Base):
    """语音签名 catalog 不可变发布历史"""

    __tablename__ = 'hasn_speech_catalog_release'

    id: Mapped[id_key] = mapped_column(init=False)
    revision: Mapped[str] = mapped_column(sa.String(16), default='', comment='catalog 原文 SHA-256 前 16 位')
    release_sequence: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='全目录单调 u64 发布序列')
    key_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='签名信任环中的稳定公钥标识')
    catalog_version: Mapped[str] = mapped_column(sa.String(64), default='', comment='签名正文中的目录版本')
    expires_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='发布信封失效时间')
    catalog_json: Mapped[str] = mapped_column(UniversalText, default='', comment='离线签名 catalog 逐字节原文')
    model_summary: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='管理展示用模型摘要，非验签权威'
    )
    published_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='发布方审计标识')
