import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnSpeechCatalogReleasePackage(Base):
    """语音 release 平台包与签名元数据快照"""

    __tablename__ = 'hasn_speech_catalog_release_package'

    id: Mapped[id_key] = mapped_column(init=False)
    release_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属不可变 catalog release')
    package_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='引用的内容寻址模型包')
    model_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='签名 catalog 中的稳定模型标识')
    model_version: Mapped[str] = mapped_column(sa.String(64), default='', comment='签名 catalog 中的模型版本')
    os: Mapped[str] = mapped_column(sa.String(32), default='', comment='目标操作系统')
    arch: Mapped[str] = mapped_column(sa.String(32), default='', comment='目标 CPU 架构')
    acceleration: Mapped[str] = mapped_column(sa.String(32), default='', comment='目标加速后端')
    installed_size: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='签名声明的安装展开字节数')
    license_name: Mapped[str] = mapped_column(sa.String(128), default='', comment='签名声明的许可证名称')
    license_url: Mapped[str] = mapped_column(sa.String(1024), default='', comment='签名声明的许可证全文 HTTPS URL')
    source_url: Mapped[str] = mapped_column(sa.String(1024), default='', comment='签名声明的权威来源 HTTPS URL')
