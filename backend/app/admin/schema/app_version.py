from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class AppVersionSchemaBase(SchemaBase):
    """应用版本基础模型"""
    app_code: str = Field(description='应用标识 (creator-flow:CreatorFlow/craft-agent:CraftAgent)')
    version: str = Field(description='语义化版本号')
    platform: str = Field(description='平台 (darwin:macOS/win32:Windows/linux:Linux)')
    arch: str = Field(description='架构 (x64:x64/arm64:ARM64)')
    download_url: str = Field(description='下载地址')
    file_hash: str = Field(description='SHA256 校验值')
    file_size: int = Field(description='文件大小（字节）')
    filename: str | None = Field(None, description='文件名')
    changelog: str | None = Field(None, description='更新日志')
    min_version: str | None = Field(None, description='最小兼容版本')
    is_force_update: bool = Field(description='是否强制更新')
    is_latest: bool = Field(description='是否为最新版本')
    status: str = Field(description='状态 (draft:草稿:blue/published:已发布:green/deprecated:已废弃:orange)')
    published_at: datetime | None = Field(None, description='发布时间')


class CreateAppVersionParam(AppVersionSchemaBase):
    """创建应用版本参数"""


class UpdateAppVersionParam(AppVersionSchemaBase):
    """更新应用版本参数"""


class DeleteAppVersionParam(SchemaBase):
    """删除应用版本参数"""

    pks: list[int] = Field(description='应用版本 ID 列表')


class GetAppVersionDetail(AppVersionSchemaBase):
    """应用版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
