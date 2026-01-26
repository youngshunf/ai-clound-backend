"""平台账号数据模型

基于 docs/16-数据库设计与同步策略.md 中的 platform_accounts 表定义
"""

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.common.model import Base, uuid4_str

if TYPE_CHECKING:
    from backend.app.project.model.project import Project


class PlatformAccount(Base):
    """平台账号表"""

    __tablename__ = 'platform_accounts'

    # 主键 UID，列名为 uid
    id: Mapped[str] = mapped_column(
        'uid',
        sa.String(36),
        primary_key=True,
        default=uuid4_str,
        sort_order=-999,
        init=False,
        comment='主键 UID',
    )

    # 关联信息：项目 UID / 用户 UID
    project_id: Mapped[str] = mapped_column(
        'project_uid', sa.String(36), sa.ForeignKey('projects.uid'), index=True, comment='项目 UID'
    )
    user_id: Mapped[str] = mapped_column(
        'user_uid', sa.String(36), index=True, comment='用户 UID'
    )

    # 关联对象
    project: Mapped["Project"] = relationship("Project", lazy="selectin", foreign_keys=[project_id])

    # 平台信息
    platform: Mapped[str] = mapped_column(sa.String(50), comment='平台类型(xiaohongshu/wechat/douyin)')
    account_id: Mapped[str] = mapped_column(sa.String(100), comment='平台侧账号ID')
    followers_count: Mapped[int] = mapped_column(sa.BigInteger, default=0, comment='粉丝数')
    session_valid: Mapped[bool] = mapped_column(default=False, comment='登录凭证是否有效')
    metadata_info: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment='额外元数据')

    # 同步状态
    is_deleted: Mapped[bool] = mapped_column(default=False, index=True, comment='是否已删除')
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None, comment='删除时间')
    last_sync_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None, comment='最近同步时间')
    server_version: Mapped[int] = mapped_column(default=0, comment='服务器版本号')
