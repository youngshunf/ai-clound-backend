import uuid

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base


class HasnCloudNodeEvents(Base):
    """云端托管节点事件流水"""

    __tablename__ = 'hasn_cloud_node_events'

    id: Mapped[UUID] = mapped_column(sa.UUID(), primary_key=True, default_factory=uuid.uuid4, init=False, comment='主键 ID')
    cloud_node_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='关联 hasn_cloud_nodes.id')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='节点 node_id（冗余，便于按设备直查）')
    event_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='事件类型 (created:已创建:blue/started:已启动:green/stopped:已停止:gray/updated:已更新:cyan/update_failed:更新失败:red/rolled_back:已回滚:orange/reauthorized:已重新授权:purple/deleted:已删除:gray/backup:已备份:green/failed:失败:red)')
    detail: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='事件明细 JSON')
