import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnServiceAccounts(Base):
    """HASN 服务号（通知来源身份）"""

    __tablename__ = 'hasn_service_accounts'

    id: Mapped[id_key] = mapped_column(init=False)
    sa_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='服务号 hasn_id（前缀 sv_）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='system', comment='类型 app|system|external')
    ref_id: Mapped[str] = mapped_column(sa.String(120), default='', comment='来源引用')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 hasn_id')
    display_name: Mapped[str] = mapped_column(sa.String(120), default='', comment='展示名')
    avatar: Mapped[str] = mapped_column(sa.String(500), default='', comment='头像 URL')
    verified: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否官方认证')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 active|disabled')
