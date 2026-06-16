"""Skill Bundle 定义表（hasn_task.skill_bundle，原 public.hasn_skill_bundle）。

owner 私有任务域资源：把多个 skill 组合成一个可复用包，任务/调度按 bundle 名称加载。
CLEAN-5 从 app/hasn 归位 app/hasn_task + 去前缀（表 hasn_skill_bundle → skill_bundle、
schema public → hasn_task）。URL `/api/v1/hasn/skill/bundles`（含 agent 面，daemon 依赖）保持不变。
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class HasnSkillBundle(HasnTaskAppBase):
    """Skill Bundle 定义表（多个 skill 的组合）"""

    __tablename__ = 'skill_bundle'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Bundle 归属 owner')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment='Bundle 名称（唯一标识）')
    display_name: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='显示名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='描述')
    skill_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(),
        default_factory=list,
        comment='Skill 名称列表，如 ["github-code-review", "test-driven-development"]',
    )
    instruction: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='可选的额外指导语，会在加载 skills 前注入'
    )
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='创建时间')
    updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='更新时间')
