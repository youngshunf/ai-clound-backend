import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class S3Storage(Base):
    """S3 存储"""

    __tablename__ = 's3_storage'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='存储名称')
    endpoint: Mapped[str] = mapped_column(sa.String(512), default='', comment='终端节点')
    access_key: Mapped[str] = mapped_column(sa.String(512), default='', comment='访问密钥')
    secret_key: Mapped[str] = mapped_column(sa.String(512), default='', comment='密钥')
    bucket: Mapped[str] = mapped_column(sa.String(64), default='', comment='存储桶')
    access: Mapped[str] = mapped_column(sa.String(16), default='private', comment='访问类型 (public/private)')
    sign_strategy: Mapped[str] = mapped_column(
        sa.String(24),
        default='s3_presign',
        comment='签名策略 (s3_presign/cdn_timestamp/qiniu_private/nginx_secure_link)',
    )
    prefix: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='前缀')
    region: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='区域')
    cdn_domain: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='CDN域名')
    remark: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='备注')
