from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnAppPublisher(Base):
    """AI-Native 应用发行方（所有权绑定）"""

    __tablename__ = 'hasn_app_publisher'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用 ID (全局唯一)')
    developer_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='开发者 hasn_id (列宽对齐 varchar(40))')
    publisher_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='发行方类型 (first_party:官方:blue/third_party:第三方:purple)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:正常:green/suspended:暂停:orange/revoked:吊销:gray)')
