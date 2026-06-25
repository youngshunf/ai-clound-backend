from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class Content(HasnCreatorAppBase):
    """内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核"""

    __tablename__ = 'content'

    id: Mapped[id_key] = mapped_column(init=False)
    content_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    created_by_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='创作分身 hasn_id（审计）')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (idea:选题:gray/researching:调研中:blue/drafting:创作中:cyan/reviewing:待审核:orange/ready:待发布:purple/published:已发布:green/analyzing:数据跟踪:teal/completed:已复盘:green/archived:已归档:gray)')
    content_tracks: Mapped[str] = mapped_column(sa.String(50), default='', comment='形态轨道（可多值逗号）article:图文/tweet:推文/video:短视频脚本')
    pipeline_mode: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='本篇自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)（缺省继承 project）')
    target_platforms: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    topic_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='来源选题（topic.id 逻辑引用）')
    viral_pattern_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='套用爆款模式（viral_pattern.id 逻辑引用）')
    playbook_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    review_status: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='审核状态 (pending:待审:orange/approved:通过:green/rejected:打回:red)')
    review_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='主人审核意见（打回时进下次教训）')
    reviewer_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    metadata_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
