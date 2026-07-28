from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnStorageEntries(Base):
    """用户云存储逻辑目录项"""

    __tablename__ = 'hasn_storage_entries'

    id: Mapped[id_key] = mapped_column(init=False)
    entry_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='目录项稳定 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属主人 hasn_id')
    asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='文件项关联的逻辑资产 ID')
    parent_entry_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='父目录项 ID；根目录为空')
    entry_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='目录项类型 (file:文件:blue/folder:文件夹:orange)')
    display_name: Mapped[str] = mapped_column(sa.String(255), default='', comment='用户可见名称')
    normalized_name: Mapped[str] = mapped_column(sa.String(255), default='', comment='服务端归一化后的冲突判定名称')
    system_category: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='系统目录分类')
    version: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='重命名与移动的乐观锁版本')
