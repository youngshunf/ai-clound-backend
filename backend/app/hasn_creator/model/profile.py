from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.utils.timezone import timezone


class Profile(HasnCreatorAppBase):
    """项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）"""

    __tablename__ = 'profile'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    niche: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    sub_niche: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    persona: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    target_audience: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    tone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='调性（轻松幽默/专业严谨/温暖治愈…自由文本）')
    keywords: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    content_pillars: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='内容支柱 ["食谱教程","厨房好物","探店"]')
    posting_frequency: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    best_posting_time: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    style_references: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    taboo_topics: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='禁区话题（合规红线硬过滤，§12）')
    bio: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    pillar_weights: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='支柱权重（进化核心）：复盘后按数据反馈调整，下次按权重选支柱')
    pillar_weights_updated_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
