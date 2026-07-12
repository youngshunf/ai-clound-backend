"""素材站目录表（A-P2-0）。"""

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_stock.model._base import HasnStockAppBase
from backend.common.model import UniversalText, id_key


class HasnStockProviders(HasnStockAppBase):
    """素材站目录（后台可配：provider/media_types/api_key加密/download_domains/enabled/priority）"""

    __tablename__ = 'hasn_stock_providers'

    id: Mapped[id_key] = mapped_column(init=False)
    # 素材站唯一标识（pexels/pixabay/coverr/…）
    provider: Mapped[str] = mapped_column(sa.String(40), default='', comment='素材站唯一标识')
    display_name: Mapped[str] = mapped_column(sa.String(80), default='', comment='展示名')
    # 支持的媒体类型 JSON 数组（image/video 子集，如 coverr 仅 ["video"]）
    media_types: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='支持的媒体类型（image/video 子集）')
    # api_key 密文（KeyEncryption Fernet 加密；明文绝不回显/入日志/进 PDC；未配为 NULL）
    api_key_cipher: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='api_key 密文（KeyEncryption 加密；未配为 NULL）'
    )
    # 该站下载直链合法域名 JSON 数组（驱动 stock.download SSRF 白名单）
    download_domains: Mapped[list] = mapped_column(
        JSONB, default_factory=list, comment='下载直链合法域名（驱动 SSRF 白名单）'
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True, comment='启用开关')
    priority: Mapped[int] = mapped_column(sa.Integer, default=100, comment='默认 failover 顺序（升序在前）')
    # 许可条款链接（随下载 meta_data 落库，供合规审查）
    license_terms_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='许可条款链接')
    remark: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='备注')
