from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnAgentTemplates(Base):
    """HASN Agent 市场模板表"""

    __tablename__ = 'hasn_agent_templates'

    id: Mapped[id_key] = mapped_column(init=False)
    template_id: Mapped[str] = mapped_column(sa.String(80), default='', unique=True, comment='模板唯一 ID')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment='模板名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='模板说明')
    avatar: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='模板默认头像')
    category: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='模板分类')
    tags: Mapped[dict | list | None] = mapped_column(postgresql.JSONB(), default=None, comment='模板标签 JSON')
    default_skills: Mapped[dict | list | None] = mapped_column(postgresql.JSONB(), default=None, comment='默认技能配置 JSON')
    default_soul_md: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='默认 SOUL.md')
    default_user_md: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='默认 USER.md')
    default_description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='默认 Agent 简介')
    default_runtime_type: Mapped[str] = mapped_column(sa.String(30), default='hermes', comment='默认 Runtime 类型')
    status: Mapped[str] = mapped_column(sa.String(20), default='active', comment='状态 (active:活跃:green/disabled:停用:orange/deleted:删除:red)')
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, comment='排序值')
    created_time: Mapped[datetime] = mapped_column(init=False, comment='创建时间')
    updated_time: Mapped[datetime | None] = mapped_column(init=False, default=None, comment='更新时间')
