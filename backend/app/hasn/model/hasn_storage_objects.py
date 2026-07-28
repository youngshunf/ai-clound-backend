from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnStorageObjects(Base):
    """用户云存储物理对象表"""

    __tablename__ = 'hasn_storage_objects'

    id: Mapped[id_key] = mapped_column(init=False)
    object_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='物理对象稳定 ID')
    owner_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='所属主人 hasn_id；平台资产为空')
    storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='对象存储配置 ID')
    object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='对象键（相对存储根）')
    key_layout: Mapped[str] = mapped_column(sa.String(16), default='', comment='对象键布局 (owner_scoped:主人隔离:blue/legacy:存量兼容:orange/platform:平台资产:green)')
    access: Mapped[str] = mapped_column(sa.String(16), default='', comment='访问类型 (private:私有:orange/public:公开:green)')
    size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='服务端校准后的权威字节数')
    sha256: Mapped[str | None] = mapped_column(sa.CHAR(), default=None, comment='服务端计算的 SHA-256')
    billable_to_owner: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否计入主人配额')
    ref_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='非删除逻辑资产引用数')
    state: Mapped[str] = mapped_column(sa.String(24), default='', comment='对象状态 (pending:待确认:orange/active:可用:green/deleting:删除中:orange/deleted:已删除:gray/missing:对象缺失:red/error:异常:red)')
