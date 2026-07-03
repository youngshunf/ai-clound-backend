from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnPendingIntents(Base):
    """HASN 第三方渠道反向 onboarding pending intent 表"""

    __tablename__ = 'hasn_pending_intents'

    id: Mapped[id_key] = mapped_column(init=False)
    intent_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Pending intent 唯一 ID (pi_{uuid})')
    channel_type: Mapped[str] = mapped_column(sa.String(30), default='', comment='渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple)')
    external_user_id: Mapped[str] = mapped_column(sa.String(120), default='', comment='第三方渠道用户 ID')
    owner_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='已解析 Owner hasn_id（可空，onboarding 后回填）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='目标 Agent hasn_id（可空）')
    conversation_hint: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='渠道会话提示 ID')
    intent_type: Mapped[str] = mapped_column(sa.String(30), default='', comment='意图类型 (onboarding:反向登录:blue/message:待投递消息:green/channel_bind:渠道绑定:purple)')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='待处理载荷摘要')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (pending:待处理:blue/consumed:已消费:green/expired:已过期:gray/revoked:已撤销:red)')
    expires_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='过期时间（默认 TTL 24h，由业务层设置）')
    consumed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='消费时间')
