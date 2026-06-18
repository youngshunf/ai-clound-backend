import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_designsystem.model._base import DesignSystemBase
from backend.common.model import id_key


class Collaborator(DesignSystemBase):
    """设计系统协作分身绑定（对齐 DECKBIND）"""

    __tablename__ = 'collaborator'

    id: Mapped[id_key] = mapped_column(init=False)
    design_system_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 design_system.id')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='协作分身 HASN ID（a_* 分身）')
    added_by: Mapped[str] = mapped_column(sa.String(64), default='', comment='添加者 HASN ID（owner）')
