from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_creator.model._base import HasnCreatorAppBase


class ContentInsight(HasnCreatorAppBase):
    """内容洞察（复盘结构化结论，进化沉淀核心）"""

    __tablename__ = 'content_insight'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    created_by_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    period: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='复盘周期（2026-W24 周报 / content:{id} 单篇）')
    insight_type: Mapped[str] = mapped_column(sa.String(24), default='', comment='类型 (pillar_performance:支柱表现:blue/hook_pattern:钩子套路:purple/timing:发布时间:orange/audience:受众:cyan/lesson:教训:gray)')
    summary: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    evidence_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='数据证据（哪些 content/publish 的哪些指标支撑）')
    action_taken: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='已据此采取的动作（调了哪个 pillar_weight / 加了哪条 viral_pattern / 改了 playbook）')
    confidence: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='置信度（样本量小时低，不轻易大改）')
