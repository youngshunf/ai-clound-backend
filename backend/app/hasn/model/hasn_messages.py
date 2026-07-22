from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnMessages(Base):
    """HASN 消息表"""

    __tablename__ = 'hasn_messages'

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='所属会话 ID')
    conversation_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本消息在所属会话内的单调序号（权威顺序事实·唯一·§4.1）')
    owner_id: Mapped[str | None] = mapped_column(
        sa.String(40),
        default=None,
        comment='所属 owner（透明视图/hasn.message.search 归属键；1:1 消息=收件方 owner，群消息留空按 conversation_id 归属）',
    )
    from_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发送方 hasn_id')
    from_type: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='发送方类型 (1:人类:blue/2:代理:green/3:系统:gray)')
    to_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='接收方标识（单聊=hasn_id，群聊=group_id 如 g:500001）')
    to_type: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='接收方类型 (1:人类:blue/2:代理:green/3:系统:gray/4:群组:purple)')
    content_type: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='内容类型 (1:文本:blue/2:图片:green/3:文件:orange/4:语音:cyan/5:卡片:purple/6:能力请求:red/7:能力响应:gray)')
    content: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='消息内容 (JSONB)')
    process_blocks: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='消息生成过程块（JSONB 数组，按产生顺序保存 stream_chunk/tool_call/status 等事件）')
    msg_type: Mapped[str] = mapped_column(sa.String(30), default='', comment='消息类型 (message:普通消息:blue/contact_request:好友请求:orange/contact_accept:接受好友:green/contact_reject:拒绝好友:red/group_invite:群邀请:purple/group_update:群变更:cyan/notification:通知:cyan/system:系统消息:gray)')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='消息状态 (1:已发送:blue/2:已送达:cyan/3:已读:green/4:已撤回:red)')
    priority: Mapped[str] = mapped_column(sa.String(10), default='', comment='优先级 (critical:紧急:red/high:高:orange/normal:普通:blue/low:低:gray)')
    reply_to_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='回复的消息 ID')
    local_id: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='客户端本地 ID（用于去重，不限格式）')
    mentions: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='@提及列表（JSONB: [{hasn_id, star_id, offset, length}]）')
    mention_all: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否 @所有人')
    context: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='消息上下文 (JSONB)')
    recalled_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='撤回时间')
    recalled_by: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='撤回者 hasn_id')
    edited_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后编辑时间')
    edit_version: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='编辑版本号')
    server_received_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='服务端接收时间')
    # doc02 §3.8：消息级设备 meta——产生该消息的节点 ID，Server 侧从认证上下文自动填、不可伪造，
    # cloud runtime 产生的填 'cloud' 哨兵；渲染边界 join hasn_nodes.node_name 解析设备名（存 node_id 不存设备名）
    origin_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产生该消息的节点 ID（Server 侧自动填·不可伪造·cloud runtime=cloud）')
    # doc14 §6.2：消息级发起溯源——产生该消息的**发起方 runtime 会话**（工作会话 id 或主会话
    # runtime session id）。同 origin_node_id 三约束：Server 侧从 AgentContext.session_id
    # （_hasn_session_id 剥离产物）自动填、入参 schema 不收、不可伪造。
    # 发起方私有：message.new 事件仅**发送方 owner** 携带，对端 owner 剥除（§7-1）。
    # daemon 据此登记 session_outbound_links，对端出结果时把结果回灌回发起方会话。
    origin_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产生该消息的发起方 runtime 会话 id（Server 侧自动填·不可伪造·仅发送方 owner 的事件携带）')
