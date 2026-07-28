from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnStorageReservations(Base):
    """用户云存储上传预占记录"""

    __tablename__ = 'hasn_storage_reservations'

    id: Mapped[id_key] = mapped_column(init=False)
    reservation_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='预占稳定 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属主人 hasn_id')
    object_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='预生成物理对象 ID')
    result_asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='成功提交后的逻辑资产 ID')
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), default='', comment='主人范围内的调用幂等键')
    request_fingerprint: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='服务端计算的请求载荷 SHA-256 指纹',
    )
    reserved_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='当前预占字节数')
    status: Mapped[str] = mapped_column(sa.String(24), default='', comment='预占状态 (reserved:已预占:orange/committed:已提交:green/released:已释放:gray/expired:已过期:red)')
    expires_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='预占过期时间')
