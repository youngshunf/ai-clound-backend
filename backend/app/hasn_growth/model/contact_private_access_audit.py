import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class ContactPrivateAccessAudit(HasnGrowthAppBase):
    """联系人私有资料访问的数据库追加式防篡改审计"""

    __tablename__ = 'contact_private_access_audit'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    actor_type: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    actor_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    action: Mapped[str] = mapped_column(sa.String(24), default='', comment=None)
    resource_type: Mapped[str] = mapped_column(sa.String(32), default='', comment=None)
    resource_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    purpose: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    trace_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    result: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    denial_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    request_metadata: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='只允许脱敏请求元数据，禁止联系方式、密文和令牌'
    )
