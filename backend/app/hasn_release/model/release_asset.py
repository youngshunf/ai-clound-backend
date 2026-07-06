from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_release.model._base import HasnReleaseAppBase
from backend.common.model import id_key, UniversalText


class ReleaseAsset(HasnReleaseAppBase):
    """发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）"""

    __tablename__ = 'release_asset'

    id: Mapped[id_key] = mapped_column(init=False)
    release_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属版本 app_release.id（级联删除）')
    platform_target: Mapped[str] = mapped_column(sa.String(32), default='', comment='平台目标（darwin-aarch64/darwin-x86_64/windows-x86_64/linux-x86_64）')
    asset_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='包类型 (installer:安装包dmg:blue/updater:热更新包:purple)')
    download_url: Mapped[str] = mapped_column(UniversalText, default='', comment='七牛 CDN 下载地址（https 直链）')
    file_name: Mapped[str] = mapped_column(sa.String(256), default='', comment='文件名')
    file_size: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='文件字节数')
    sha256: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='文件 sha256（完整性校验）')
    signature: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='minisign 签名（仅 updater；Tauri 客户端验签用）')
    download_count: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='下载计数（经计数重定向端点累加）')
