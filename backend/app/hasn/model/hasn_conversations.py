import uuid

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText


class HasnConversations(Base):
    """HASN 会话表"""

    __tablename__ = 'hasn_conversations'

    id: Mapped[UUID] = mapped_column(sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='会话主键 ID')
    type: Mapped[str] = mapped_column(sa.String(10), default='', comment='会话类型 (direct:单聊:blue/group:群聊:green)')
    relation_type: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='关系类型 (social:社交:blue/commerce:商业:orange/service:履约:green/professional:专业:purple/platform:平台:cyan)')
    participant_a_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='参与方 A hasn_id（单聊必填，群聊=创建者）')
    participant_b_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='参与方 B hasn_id（单聊必填，群聊为 NULL）')
    participant_a_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='参与方 A 类型 (human:人类:blue/agent:代理:green)')
    participant_b_type: Mapped[str | None] = mapped_column(sa.String(10), default=None, comment='参与方 B 类型 (human:人类:blue/agent:代理:green)')
    trade_session_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='关联交易会话 ID')
    group_id: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='群组公开标识（格式: g:500001，type=group 时有值）')
    group_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='群名称（type=group 时有值）')
    group_description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='群描述（type=group 时有值）')
    group_avatar_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='群头像 URL（type=group 时有值）')
    group_owner_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='群主 hasn_id（type=group 时有值）')
    agent_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='Agent 发言策略 (free:自由:green/mention_only:@提及:blue/silent:静默:gray/no_agent:禁止:red)')
    join_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='加入策略 (open:开放:green/invite_only:仅邀请:blue/approval:需审核:orange)')
    max_members: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='最大成员数')
    allow_invite: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='成员是否可邀请')
    allow_member_invite_agent: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否允许普通成员拉分身进群 (true:允许/false:仅群主管理员)')
    mute_all: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='全员禁言')
    member_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='当前成员数')
    last_message_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='最后一条消息 ID')
    last_message_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后消息时间')
    last_message_preview: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='最后消息预览')
    last_message_from: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='最后消息发送方 hasn_id')
    message_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='消息总数')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:活跃:green/archived:已归档:gray/disbanded:已解散:red/unreachable:不可达:orange)')
    # doc02 §3.2：会话元数据版本号——成员/群名/策略变更时 +1，供 daemon 按 revision 判断本地镜像是否过期
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='会话元数据版本号（成员/群名/策略变更 +1，供 daemon 判镜像是否过期）')
    # doc14 §6.5（E 刀）：差事背景——分身自撰的一句话，说明「这个对外会话是来干什么的」。
    # 仅**发起层建会话时**写入（既有会话不覆盖）；是发送方 owner 的**私有框定**：
    # 会话对象投影只对 mission_note_owner_id 序列化，对端 owner 一律裁剪（§7-1 同精神）。
    # daemon 在 peer 会话首次派发时随 IdentityPeer 同层注入，让对端会话知道差事背景。
    mission_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='差事背景（分身自撰一句话·发起层建会话时写入·发送方 owner 私有）')
    # mission_note 的归属 owner——投影裁剪判据。显式落列而非从首条消息反推「发送方」（零推断）。
    mission_note_owner_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='mission_note 的归属 owner hasn_id（投影只对该 owner 序列化 mission_note）')
