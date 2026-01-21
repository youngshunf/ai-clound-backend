"""平台账号数据模型

基于 docs/16-数据库设计与同步策略.md 中的 platform_accounts 表定义
"""

from datetime import datetime
from typing import Optional, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.common.model import Base, id_key
from backend.database.db import uuid4_str

if TYPE_CHECKING:
    from backend.app.project.model.project import Project


class PlatformAccount(Base):
    """平台账号表"""

    __tablename__ = 'platform_accounts'

    id: Mapped[id_key] = mapped_column(init=False)
    uuid: Mapped[str] = mapped_column(sa.String(64), init=False, default_factory=uuid4_str, unique=True)

    # 关联信息
    project_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey('projects.id'), index=True, comment='项目ID')
    user_id: Mapped[int] = mapped_column(sa.BigInteger, index=True, comment='用户ID')

    # 关联对象
    project: Mapped["Project"] = relationship("Project", lazy="selectin", foreign_keys=[project_id])

    @property
    def project_uuid(self) -> str:
        return self.project.uuid if self.project else ""


    # 平台信息
    platform: Mapped[str] = mapped_column(sa.String(50), comment='平台类型(xiaohongshu/wechat/douyin)')
    account_id: Mapped[str] = mapped_column(sa.String(100), comment='平台侧账号ID')
    account_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='账号名称/昵称')
    
    # 账号状态与资料
    avatar_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='头像URL')
    followers_count: Mapped[int] = mapped_column(sa.BigInteger, default=0, comment='粉丝数')
    following_count: Mapped[int] = mapped_column(sa.Integer, default=0, comment='关注数')
    posts_count: Mapped[int] = mapped_column(sa.Integer, default=0, comment='作品数')
    
    is_active: Mapped[bool] = mapped_column(default=True, comment='是否启用')
    session_valid: Mapped[bool] = mapped_column(default=False, comment='登录凭证是否有效')
    metadata_info: Mapped[dict] = mapped_column(JSONB, default_factory=dict, comment='额外元数据')

    # 同步状态
    is_deleted: Mapped[bool] = mapped_column(default=False, index=True, comment='是否已删除')
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None, comment='删除时间')
    last_sync_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), default=None, comment='最近同步时间')
    server_version: Mapped[int] = mapped_column(default=0, comment='服务器版本号')
