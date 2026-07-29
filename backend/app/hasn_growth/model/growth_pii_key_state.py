import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class GrowthPiiKeyState(HasnGrowthAppBase):
    """Growth PII 写入密钥版本单例栅栏"""

    __tablename__ = 'growth_pii_key_state'

    id: Mapped[int] = mapped_column(sa.SMALLINT(), primary_key=True, default=1, init=False)
    min_encryption_write_version: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='允许写入的最低加密密钥版本'
    )
    min_hmac_write_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='允许写入的最低 HMAC 密钥版本')
