from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnClients(Base):
    """HASN 客户端设备表"""

    __tablename__ = 'hasn_clients'

    id: Mapped[id_key] = mapped_column(init=False)
    client_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='客户端唯一标识 (格式: c_{uuid_short})')
    user_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属 Human 的 hasn_id（格式: h_xxx）')
    client_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='客户端类型 (desktop:桌面端:blue/mobile:手机端:green/web:网页端:orange/cloud:云端:purple)')
    device_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='设备名称')
    device_info: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='设备信息 (JSONB)')
    last_seen_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后活跃时间')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:活跃:green/disabled:已禁用:orange/deleted:已删除:red)')
