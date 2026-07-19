import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnSpeechPackage(Base):
    """语音模型不可变内容寻址包登记"""

    __tablename__ = 'hasn_speech_package'

    id: Mapped[id_key] = mapped_column(init=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), default='', comment='上传原始字节的规范小写 SHA-256，全局唯一')
    storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='实际承载对象的公共 S3 存储 ID')
    object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='由 SHA-256 派生的不可变对象 key')
    size: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='对象字节数')
    object_etag: Mapped[str | None] = mapped_column(
        sa.String(256), default=None, comment='完整 SHA-256 复核时对应的对象存储不可变版本标识'
    )
    content_type: Mapped[str] = mapped_column(
        sa.String(128), default='', comment='对象媒体类型，模型包固定为 application/zip'
    )
