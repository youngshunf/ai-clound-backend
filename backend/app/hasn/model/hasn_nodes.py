from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnNodes(Base):
    """HASN Node 主表"""

    __tablename__ = 'hasn_nodes'

    id: Mapped[id_key] = mapped_column(init=False)
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='节点唯一标识 (格式: n_{uuid_short})')
    user_id: Mapped[int | None] = mapped_column(sa.BigInteger(), default=None, comment='平台用户 ID（桌面端/唤星账号场景）')
    allowed_owner_hasn_ids: Mapped[list[str] | None] = mapped_column(postgresql.JSONB(), default=None, comment='允许绑定的 Owner 列表 JSON（NULL/空数组表示不限制，SDK 场景可指定白名单）')
    node_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='节点类型 (desktop:桌面端:blue/mobile:手机端:green/web:网页端:orange/cloud:云端:purple/sdk:SDK:cyan)')
    node_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='节点名称')
    device_fingerprint: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='设备指纹（用于幂等创建和识别同一设备）')
    device_platform: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='设备平台 (macos:macOS:blue/windows:Windows:cyan/linux:Linux:green/ios:iOS:purple/android:Android:orange/web:Web:gray/sdk:SDK:yellow/server:Server:red)')
    app_version: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='接入端应用版本')
    ip_address: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近一次连接的客户端 IP（v4/v6）')
    ip_location: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='IP 归属地（GeoLite2 离线解析，缺库时留空表示未知）')
    node_info: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='节点信息 JSON')
    node_key_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='Node Key 的 SHA256 哈希')
    capacity: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='最大 Agent 承载量')
    created_by_owner_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='初始创建 Owner（仅审计用途）')
    last_seen_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后活跃时间')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:活跃:green/disabled:已禁用:orange/deleted:已删除:red)')
