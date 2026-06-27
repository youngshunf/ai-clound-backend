from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnAppBetaAccess(Base):
    """AI-Native 应用灰度内测访问（云端权威）"""

    __tablename__ = 'hasn_app_beta_access'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用唯一标识')
    subject_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='访问主体 (owner:个人:blue/enterprise:企业:purple)')
    subject_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主体 ID（owner=hasn_id / enterprise=enterprise_id）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (invite:邀请:blue/apply:申请:orange)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='审批状态 (pending:待审:orange/approved:已通过:green/rejected:已拒绝:red)')
    note: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='申请理由 / 审批备注')
    decided_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='审批人（admin user id 或 hasn_id；邀请时为操作管理员）')
    decided_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='审批 / 邀请时间')
