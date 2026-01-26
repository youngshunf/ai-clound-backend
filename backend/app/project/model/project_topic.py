"""项目私有选题数据模型

与 sys_topic 保持同口径字段，用于按 project 维度存储用户私有选题，并支持端侧同步。
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.common.model import Base, uuid4_str


class ProjectTopic(Base):
    """项目私有选题表"""

    __tablename__ = "project_topics"

    # 主键 UID，列名为 uid
    id: Mapped[str] = mapped_column(
        'uid',
        sa.String(36),
        primary_key=True,
        default=uuid4_str,
        sort_order=-999,
        init=False,
        comment="主键 UID",
    )

    # 关联项目 / 用户 UID
    project_id: Mapped[str] = mapped_column(
        'project_uid',
        sa.String(36),
        sa.ForeignKey("projects.uid"),
        index=True,
        comment="项目 UID",
    )
    user_id: Mapped[str] = mapped_column(
        'user_uid', sa.String(36), index=True, comment="用户 UID"
    )

    project = relationship("Project", lazy="selectin", foreign_keys=[project_id])

    title: Mapped[str] = mapped_column(sa.String(255), comment="选题标题")
    potential_score: Mapped[float] = mapped_column(sa.Float, default=0.0, comment="选题潜力分数")
    heat_index: Mapped[float] = mapped_column(sa.Float, default=0.0, comment="热度指数")
    reason: Mapped[str] = mapped_column(sa.Text, default="", comment="推荐理由")

    keywords: Mapped[list[str]] = mapped_column(JSONB, default_factory=list, comment="标签/关键词")
    platform_heat: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment="平台热度分布")
    heat_sources: Mapped[list] = mapped_column(JSONB, default_factory=list, comment="热度来源")
    trend: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment="趋势走势")
    industry_tags: Mapped[list[str]] = mapped_column(JSONB, default_factory=list, comment="适配行业")
    target_audience: Mapped[list | dict] = mapped_column(JSONB, default_factory=list, comment="目标人群")
    creative_angles: Mapped[list[str]] = mapped_column(JSONB, default_factory=list, comment="创作角度")
    content_outline: Mapped[list | dict] = mapped_column(JSONB, default_factory=list, comment="内容结构要点")
    format_suggestions: Mapped[list[str]] = mapped_column(JSONB, default_factory=list, comment="形式建议")
    material_clues: Mapped[list | dict] = mapped_column(JSONB, default_factory=list, comment="素材线索")
    risk_notes: Mapped[list[str]] = mapped_column(JSONB, default_factory=list, comment="风险点")
    source_info: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment="来源信息")

    batch_date: Mapped[date | None] = mapped_column(sa.Date, default=None, index=True, comment="生成批次日期")
    source_uid: Mapped[str | None] = mapped_column(sa.String(128), default=None, index=True, comment="幂等键")

    status: Mapped[int] = mapped_column(sa.Integer, default=0, comment="状态(0:待选 1:已采纳 2:已忽略)")

    is_deleted: Mapped[bool] = mapped_column(default=False, index=True, comment="是否已删除")
    deleted_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        default=None,
        comment="删除时间",
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        default=None,
        comment="最近同步时间",
    )
    server_version: Mapped[int] = mapped_column(default=0, comment="服务器版本号")
