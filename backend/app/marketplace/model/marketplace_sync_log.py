from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.marketplace.model._base import MarketplaceBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class MarketplaceSyncLog(MarketplaceBase):
    """技能市场同步日志表"""

    __tablename__ = 'marketplace_sync_log'

    id: Mapped[id_key] = mapped_column(init=False)
    sync_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='同步类型 (github:GitHub同步:blue/clawhub:ClawHub同步:green)')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='同步状态 (success:成功:green/failed:失败:red/partial:部分成功:orange)')
    items_synced: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='成功同步数量')
    items_failed: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='失败数量')
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='错误信息')
    git_commit_before: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='同步前的 commit hash')
    git_commit_after: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='同步后的 commit hash')
    started_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='开始时间')
    completed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='完成时间')
