from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_designsystem.model._base import DesignSystemBase
from backend.common.model import TimeZone, id_key


class DesignSystem(DesignSystemBase):
    """设计系统（云端权威）"""

    __tablename__ = 'design_system'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）'
    )
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='设计系统名称')
    slug: Mapped[str] = mapped_column(sa.String(128), default='', comment='owner 维度唯一 slug（同 owner 不可重复）')
    category: Mapped[str | None] = mapped_column(
        sa.String(48), default=None, comment='分类（可空，如 saas/editorial/playful…）'
    )
    source_kind: Mapped[str] = mapped_column(
        sa.String(32),
        default='generated',
        comment='来源 (generated:生成:violet/imported_shadcn:shadcn导入:blue/imported_github:GitHub导入:blue/imported_screenshot:截图导入:cyan/seed:官方内置:green)',
    )
    score: Mapped[int | None] = mapped_column(sa.SMALLINT(), default=None, comment='当前版评分 0-100（token 契约评分）')
    grade: Mapped[str | None] = mapped_column(
        sa.String(16), default=None, comment='等级 (excellent:优秀:green/good:良好:blue/fair:一般:orange/poor:较差:red)'
    )
    recommend_rebuild: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否建议重建（评分过低）')
    is_builtin: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='是否官方内置（seed，跨 owner 只读可见）'
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='归属企业 ID（null=个人；非空=企业私有，引用 public.hasn_enterprise）'
    )
    current_revision_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='当前版 revision.id（指向最新 revision）'
    )
    content_hash: Mapped[str] = mapped_column(
        sa.String(128), default='', comment='当前版内容 hash（供同步 revision diff）'
    )
    # AppCollab（doc21 / 实施21 AC-P3）：协作分身绑定（与 deck DECKBIND 同模型——主会话派发阵营）。
    bound_agent_id: Mapped[str | None] = mapped_column(
        sa.String(40),
        default=None,
        comment='协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；生成它的分身，负责后续精修，改绑需二次确认）',
    )
    deleted_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='软删时间（非空=已删，不物理删以便同步感知）'
    )
