"""项目数据模型

基于 docs/16-数据库设计与同步策略.md 中的 projects 表定义
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, uuid_pk


class Project(Base):
    """项目表 - 工作区的核心上下文"""

    __tablename__ = 'projects'

    id: Mapped[uuid_pk] = mapped_column(init=False)

    # 所属用户
    user_id: Mapped[str] = mapped_column(sa.String(36), index=True, comment='用户ID')

    # 基本信息
    name: Mapped[str] = mapped_column(sa.String(100), comment='项目名称')
    description: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='项目描述')

    # 行业领域
    industry: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='行业')
    sub_industries: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='子行业列表')

    # 品牌信息
    brand_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='品牌名称')
    brand_tone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='品牌调性')
    brand_keywords: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='品牌关键词')

    # 内容定位
    topics: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='关注话题')
    keywords: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='内容关键词')
    account_tags: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='账号标签')

    # 偏好设置
    preferred_platforms: Mapped[list] = mapped_column(JSONB, default_factory=list, comment='偏好平台')
    content_style: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='内容风格')
    settings: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment='其他设置')

    # 状态标记
    is_default: Mapped[bool] = mapped_column(default=False, comment='是否默认项目')
    is_deleted: Mapped[bool] = mapped_column(default=False, index=True, comment='是否已删除')
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None, comment='删除时间')

    # 同步元数据
    server_version: Mapped[int] = mapped_column(default=0, comment='服务器版本号')
