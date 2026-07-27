from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key
from backend.database.schema_names import IM_SCHEMA


class HasnAssetGrants(Base):
    """HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）"""

    __tablename__ = 'hasn_asset_grants'
    __table_args__ = {'schema': IM_SCHEMA}

    id: Mapped[id_key] = mapped_column(init=False)
    asset_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='资产 ID (关联 hasn_assets.asset_id)')
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='授权作用域会话 ID (该会话参与者可读该资产)')
