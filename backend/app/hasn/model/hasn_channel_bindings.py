from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnChannelBindings(Base):
    """HASN Channel Binding 表"""

    __tablename__ = 'hasn_channel_bindings'

    id: Mapped[id_key] = mapped_column(init=False)
    binding_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Channel Binding 唯一 ID (cb_{uuid})')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner hasn_id')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='绑定 Agent hasn_id（可空表示 Owner 级绑定）')
    channel_type: Mapped[str] = mapped_column(sa.String(30), default='', comment='渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple)')
    external_user_id: Mapped[str] = mapped_column(sa.String(120), default='', comment='第三方渠道用户 ID')
    external_chat_id: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='第三方渠道会话/群 ID（可空）')
    display_name: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='渠道侧展示名')
    binding_scope: Mapped[str] = mapped_column(sa.String(30), default='', comment='绑定范围 (owner:Owner:blue/agent:Agent:green/group:群聊:purple)')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:生效中:green/revoked:已吊销:red/deleted:已删除:gray)')
    policy_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='渠道策略摘要')
    last_inbound_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近入站时间')
    last_outbound_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近出站时间')
    revoked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='吊销时间')
