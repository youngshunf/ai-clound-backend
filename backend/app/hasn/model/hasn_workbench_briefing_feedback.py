from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnWorkbenchBriefingFeedback(Base):
    """HASN 工作台简报反馈（云端权威）"""

    __tablename__ = 'hasn_workbench_briefing_feedback'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 HASN ID（owner 隔离键）')
    period: Mapped[str] = mapped_column(sa.String(10), default='', comment='所属简报周期 YYYY-MM-DD')
    item_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='被标记的关注项 item_id')
    action: Mapped[str] = mapped_column(sa.String(32), default='', comment='反馈动作 (dismiss:已知道:gray/done:已处理:green)')
    source_ref: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='关注项溯源 source.ref（去重用）')
    note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='备注（可空）')
