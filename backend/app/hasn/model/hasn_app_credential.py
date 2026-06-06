from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnAppCredential(Base):
    """AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）"""

    __tablename__ = 'hasn_app_credential'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用 ID（如 knowledge）')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    app_instance_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属应用实例 ID（hasn_app_instance.id）')
    credential_ref: Mapped[str] = mapped_column(sa.String(), default='', comment='用户级凭据密文（key_encryption.encrypt，绝不存明文）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待激活:gray/active:已激活:green/revoked:已吊销:red/error:错误:orange)')
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最近一次 provision/刷新错误')
    config: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='app 私有凭据元数据（如 ragflow_user_id/ragflow_tenant_id）')
