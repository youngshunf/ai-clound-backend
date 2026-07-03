import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_diag.model._base import HasnDiagAppBase
from backend.common.model import id_key


class ErrorIssueSeen(HasnDiagAppBase):
    """affected 计数辅助表（INSERT ON CONFLICT DO NOTHING，插入成功才 affected_*_count += 1；累计口径 TTL 不回缩）"""

    __tablename__ = 'error_issue_seen'

    id: Mapped[id_key] = mapped_column(init=False)
    fingerprint: Mapped[str] = mapped_column(sa.String(64), default='', comment='归类键')
    subject_type: Mapped[str] = mapped_column(sa.String(8), default='', comment='主体类型 (owner:主人/node:设备)')
    subject_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='主体 id（owner_hasn_id 或 node_id）')
