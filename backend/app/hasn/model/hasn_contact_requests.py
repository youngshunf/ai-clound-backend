from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key
from backend.database.schema_names import IM_SCHEMA


class HasnContactRequests(Base):
    """HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）"""

    __tablename__ = 'hasn_contact_requests'
    __table_args__ = {'schema': IM_SCHEMA}

    id: Mapped[id_key] = mapped_column(init=False)
    from_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起方 hasn_id（恒 human）')
    from_type: Mapped[str] = mapped_column(
        sa.String(10), default='', comment='发起方类型 (human:人类:blue/agent:代理:green)'
    )
    to_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='目标 hasn_id（解析后恒 human）')
    to_type: Mapped[str] = mapped_column(
        sa.String(10), default='', comment='目标类型 (human:人类:blue/agent:代理:green)'
    )
    to_owner_id: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='审批人 hasn_id（=目标本人，agent 目标则解析为其主人）'
    )
    relation_type: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='关系类型 (social:社交:blue/commerce:商业:orange/service:履约:green/professional:专业:purple/platform:平台:cyan)',
    )
    requested_trust_level: Mapped[int] = mapped_column(
        sa.SMALLINT(), default=0, comment='请求授予的信任等级（通过时落到 hasn_contacts.trust_level）'
    )
    message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='请求附言')
    status: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='状态 (pending:待处理:blue/accepted:已通过:green/rejected:已拒绝:red/withdrawn:已撤回:gray/expired:已过期:gray)',
    )
    decided_by: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='回应人 hasn_id')
    decided_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='回应时间')
    resulting_contact_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='通过后建立的 hasn_contacts 行 ID（审计链）'
    )
    channel_source: Mapped[str | None] = mapped_column(
        sa.String(30),
        default=None,
        comment='来源渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple/manual:好友请求:gray/system:系统:orange)',
    )
    add_source: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='添加来源')
