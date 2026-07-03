import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnAppInstance(Base):
    """AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）"""

    __tablename__ = 'hasn_app_instance'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用 ID (关联 hasn_ai_native_app_manifest.app_id)')
    scope: Mapped[str] = mapped_column(sa.String(16), default='', comment='作用域 (public:公共:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID (scope=enterprise 必填；public 为 null)')
    endpoint: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='实例后端基地址 (gateway_internal 可空=云端自身)')
    transport_default: Mapped[str] = mapped_column(sa.String(24), default='', comment='默认传输 (gateway_internal:就地:gray/cloud_relay:云端转发:blue/daemon_direct:本地直连:green/frontend_direct:前端直连:orange)')
    credential_ref: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='接入凭据引用 (指向加密凭据/KMS，不存明文)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:可用:green/pending_config:待配置:orange/disabled:停用:gray)')
    config: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='实例配置')
