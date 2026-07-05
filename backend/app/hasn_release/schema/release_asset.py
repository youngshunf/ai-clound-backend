from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ReleaseAssetSchemaBase(SchemaBase):
    """发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）基础模型"""
    release_id: int = Field(description='所属版本 app_release.id（级联删除）')
    platform_target: str = Field(description='平台目标（darwin-aarch64/darwin-x86_64/windows-x86_64/linux-x86_64）')
    asset_kind: str = Field(description='包类型 (installer:安装包dmg:blue/updater:热更新包:purple)')
    download_url: str = Field(description='七牛 CDN 下载地址（https 直链）')
    file_name: str = Field(description='文件名')
    file_size: int = Field(description='文件字节数')
    sha256: str | None = Field(None, description='文件 sha256（完整性校验）')
    signature: str | None = Field(None, description='minisign 签名（仅 updater；Tauri 客户端验签用）')
    download_count: int = Field(description='下载计数（经计数重定向端点累加）')


class CreateReleaseAssetParam(ReleaseAssetSchemaBase):
    """创建发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）参数"""


class UpdateReleaseAssetParam(ReleaseAssetSchemaBase):
    """更新发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）参数"""


class DeleteReleaseAssetParam(SchemaBase):
    """删除发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）参数"""

    pks: list[int] = Field(description='发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新） ID 列表')


class GetReleaseAssetDetail(ReleaseAssetSchemaBase):
    """发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
