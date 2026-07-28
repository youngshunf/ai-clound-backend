from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnStorageAccounts(Base):
    """用户云存储账户投影"""

    __tablename__ = 'hasn_storage_accounts'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属主人 hasn_id')
    quota_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='当前生效配额字节数')
    used_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='已确认计费对象字节数')
    reserved_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='上传中预占字节数')
    quota_source: Mapped[str] = mapped_column(sa.String(32), default='', comment='配额来源 (free_policy:免费政策:blue/subscription:订阅合同:green/admin_override:管理覆盖:orange)')
    quota_version: Mapped[str] = mapped_column(sa.String(64), default='', comment='免费政策或合同版本')
    source_subscription_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='付费合同来源')
    quota_valid_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='当前配额有效期终点')
    state: Mapped[str] = mapped_column(sa.String(24), default='', comment='账户状态 (active:正常:green/over_quota:超额:orange/suspended:暂停:red)')
