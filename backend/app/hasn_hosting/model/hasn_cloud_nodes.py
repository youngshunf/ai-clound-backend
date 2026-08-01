import uuid

from datetime import datetime
from uuid import UUID
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnCloudNodes(Base):
    """云端托管节点状态"""

    __tablename__ = 'hasn_cloud_nodes'

    id: Mapped[UUID] = mapped_column(sa.UUID(), primary_key=True, default_factory=uuid.uuid4, init=False, comment='主键 ID')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='对应 hasn_nodes.node_id')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='平台用户 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 HASN ID')
    host: Mapped[str] = mapped_column(sa.String(64), default='', comment='承载宿主标识（MVP 单宿主也必须落值）')
    container_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='hosting-agent 侧容器标识')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (provisioning:创建中:blue/starting:启动中:cyan/online:在线:green/stopped:已停止:gray/updating:更新中:orange/failed:失败:red/deleting:删除中:orange/deleted:已删除:gray)')
    failure_reason: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='失败原因码（subscription_invalid/authorization_code_expired/authorization_code_consumed/credential_invalid/resource_exhausted/image_pull_failed/container_crashed/daemon_not_online/internal_error）')
    failure_detail: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='人可读失败详情')
    image_version: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='镜像版本号')
    image_digest: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='镜像 digest（以 digest 为准，不信 tag）')
    credential_session_uuid: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='设备凭据所在 JWT session，用于单独吊销')
    retain_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='订阅到期后的数据保留截止时刻')
    last_backup_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近一次卷备份时刻，NULL 表示尚无备份')
    online_since: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='本次上线起始时刻')
