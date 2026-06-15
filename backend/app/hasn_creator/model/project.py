from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_creator.model._base import HasnCreatorAppBase


class Project(HasnCreatorAppBase):
    """运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度"""

    __tablename__ = 'project'

    id: Mapped[id_key] = mapped_column(init=False)
    project_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）')
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='负责运营的人 hasn_id（角色裁剪键；personal=owner_hasn_id）')
    assignee_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='负责运营的分身 hasn_id（§8.4 主脑 re-bind）')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment=None)
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    primary_platform: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='主平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    pipeline_mode: Mapped[str] = mapped_column(sa.String(16), default='', comment='运营自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)')
    playbook_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='采用的账号打法（playbook.id 逻辑引用）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:运营中:green/paused:已暂停:orange/archived:已归档:gray)')
