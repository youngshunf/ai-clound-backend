from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnWorkbenchBriefing(Base):
    """HASN 工作台每日关注简报（云端权威）"""

    __tablename__ = 'hasn_workbench_briefing'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 HASN ID（owner 隔离键）')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='产出该简报的主脑 HASN ID')
    period: Mapped[str] = mapped_column(sa.String(10), default='', comment='覆盖周期 YYYY-MM-DD（主人本地日期）')
    state: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (generating:生成中:blue/ready:就绪:green/failed:失败:red)')
    document_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='完整 BriefingDocument（JSONB）')
    generated_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='产出时间')
