from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key


class HasnContacts(Base):
    """HASN 联系人关系表"""

    __tablename__ = 'hasn_contacts'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(36), default='', comment='关系拥有者 hasn_id')
    peer_id: Mapped[str] = mapped_column(sa.String(36), default='', comment='对方 hasn_id')
    peer_owner_id: Mapped[str | None] = mapped_column(
        sa.String(36), default=None, comment='对方归属人 hasn_id (peer 自己的 owner)',
    )
    peer_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='对方类型 (human:人类:blue/agent:代理:green)')
    relation_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='关系类型 (social:社交:blue/commerce:商业:orange/service:履约:green/professional:专业:purple/platform:平台:cyan)')
    trust_level: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='信任等级 (0:已拉黑:red/1:陌生人:gray/2:普通联系人:blue/3:朋友:green/4:高信任:orange/5:所有者:purple)')
    scope: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='关系作用域 (JSONB)')
    custom_permissions: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='自定义权限覆盖 (JSONB)')
    nickname: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='备注名')
    tags: Mapped[list[str] | None] = mapped_column(
        postgresql.ARRAY(sa.String()), default=None, comment='分组标签',
    )
    subscription: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否订阅推送')
    channel_source: Mapped[str | None] = mapped_column(sa.String(30), default=None, comment='来源渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple/manual:手动:gray/system:系统:orange)')
    add_source: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='添加来源')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (pending:待处理:blue/connected:已连接:green/blocked:已拉黑:red/archived:已归档:gray)')
    request_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='好友请求附言')
    auto_expire: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='自动过期时间')
    connected_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='建立连接时间')
    last_interaction_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后互动时间')
    interaction_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='互动次数')
