import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnAssets(Base):
    """HASN 资产注册表（消息附件/私有文档等对象的逻辑引用与元数据）"""

    __tablename__ = 'hasn_assets'

    id: Mapped[id_key] = mapped_column(init=False)
    asset_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='资产 ID (hasn://asset/{asset_id})')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属主人 hasn_id')
    access: Mapped[str] = mapped_column(sa.String(16), default='', comment='访问类型 (public:公开:green/private:私有:orange)')
    storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='存储空间 ID (关联 s3_storage.id)')
    object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='对象 key (相对 opendal root，不含 provider URL)')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='资产类型 (image:图片:blue/voice:语音:purple/file:文件:gray)')
    mime: Mapped[str] = mapped_column(sa.String(128), default='', comment='MIME 类型')
    size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='字节大小')
    content_sha256: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='资产内容 sha256；本地原件快照据此幂等上传'
    )
    width: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='图片宽 (px)')
    height: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='图片高 (px)')
    duration_ms: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='语音时长 (毫秒)')
    transcript: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='语音转写文本 (STT 结果)')
    thumbnail_asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='缩略图资产 ID')
    extract_status: Mapped[str] = mapped_column(sa.String(16), default='', comment='抽取状态 (pending:待处理:orange/done:完成:green/unsupported:不支持:gray/stt_unavailable:STT不可用:red)')
