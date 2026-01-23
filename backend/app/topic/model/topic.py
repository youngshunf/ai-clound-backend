from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.topic.model.industry import Industry
from backend.common.model import Base, id_key


class Topic(Base):
    """选题表"""

    __tablename__ = 'sys_topic'

    id: Mapped[id_key] = mapped_column(init=False)
    title: Mapped[str] = mapped_column(String(255), comment='选题标题')
    industry_id: Mapped[int | None] = mapped_column(ForeignKey('sys_industry.id'), default=None, comment='关联行业ID')
    potential_score: Mapped[float] = mapped_column(Float, default=0.0, comment='选题潜力分数')
    heat_index: Mapped[float] = mapped_column(Float, default=0.0, comment='热度指数')
    reason: Mapped[str | None] = mapped_column(Text, default=None, comment='推荐理由')
    keywords: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='标签/关键词')
    platform_heat: Mapped[dict | None] = mapped_column(JSONB, default=None, comment='平台热度分布')
    heat_sources: Mapped[list[dict] | list[str] | None] = mapped_column(JSONB, default=None, comment='热度来源')
    trend: Mapped[dict | list[dict] | None] = mapped_column(JSONB, default=None, comment='趋势走势')
    industry_tags: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='适配行业')
    target_audience: Mapped[list[str] | dict | None] = mapped_column(JSONB, default=None, comment='目标人群')
    creative_angles: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='创作角度')
    content_outline: Mapped[list[str] | dict | None] = mapped_column(JSONB, default=None, comment='内容结构要点')
    format_suggestions: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='形式建议(图文/短视频/直播/合集/系列)')
    material_clues: Mapped[list[str] | dict | None] = mapped_column(JSONB, default=None, comment='素材线索')
    risk_notes: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='风险点')
    source_info: Mapped[dict | None] = mapped_column(JSONB, default=None, comment='来源信息')
    batch_date: Mapped[date | None] = mapped_column(Date, default=None, comment='生成批次日期')
    source_uid: Mapped[str | None] = mapped_column(String(128), default=None, comment='幂等键')
    status: Mapped[int] = mapped_column(Integer, default=0, comment='状态(0:待选 1:已采纳 2:已忽略)')

    # 关联关系
    industry: Mapped[Industry] = relationship(init=False)
