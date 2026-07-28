from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnAssetBindings(Base):
    """逻辑资产与业务资源的权威反向引用"""

    __tablename__ = 'hasn_asset_bindings'

    id: Mapped[id_key] = mapped_column(init=False)
    binding_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='绑定稳定 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='资产所属主人 hasn_id')
    asset_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='逻辑资产 ID')
    resource_uri: Mapped[str] = mapped_column(sa.String(1024), default='', comment='引用资产的稳定资源 URI')
    role: Mapped[str] = mapped_column(sa.String(32), default='', comment='引用角色')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='绑定状态 (active:有效:green/deleted:已删除:gray)')
