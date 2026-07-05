"""桌面端发布模块定制 schema（发布/资产/CI 回调/Tauri updater manifest）。

设计事实源：docs/hasn-node设计文档/桌面端发布与自动更新/00 §3–§5。
- 二进制托管七牛 CDN，云端只存元数据 + CDN url（CI/管理端预上传后回传）。
- updater 资产携 minisign signature；Tauri 客户端持公钥自行验签（云端只存储+下发）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

# 平台目标枚举（近期 macOS 双架构；预留 windows/linux）
PLATFORM_TARGETS = ('darwin-aarch64', 'darwin-x86_64', 'windows-x86_64', 'linux-x86_64')
ASSET_KINDS = ('installer', 'updater')
CHANNELS = ('stable', 'beta')


class ReleaseAssetInput(SchemaBase):
    """发布资产入参（CI/管理端预上传七牛后回传元数据）"""

    platform_target: str = Field(description='平台目标 darwin-aarch64/darwin-x86_64/...')
    asset_kind: str = Field(description='包类型 installer(dmg 下载页)/updater(app.tar.gz 热更新)')
    download_url: str = Field(description='七牛 CDN https 直链')
    file_name: str = Field(description='文件名')
    file_size: int = Field(default=0, description='字节数')
    sha256: str | None = Field(default=None, description='sha256 完整性校验')
    signature: str | None = Field(default=None, description='minisign 签名（仅 updater 必填）')


class ReleaseAssetDetail(SchemaBase):
    """发布资产出参"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    platform_target: str
    asset_kind: str
    download_url: str
    file_name: str
    file_size: int
    sha256: str | None = None
    signature: str | None = None
    download_count: int


class PublishReleaseRequest(SchemaBase):
    """发布/上传新版本入参（管理端手动上传 + CI 回调共用核心）"""

    version: str = Field(description='semver，如 1.2.0')
    channel: str = Field(default='stable', description='stable/beta')
    release_notes_md: str | None = Field(default=None, description='更新日志（中）')
    release_notes_en_md: str | None = Field(default=None, description='更新日志（英）')
    source: str = Field(default='manual', description='manual/github')
    github_run_id: str | None = Field(default=None, description='GitHub run id（source=github）')
    set_latest: bool = Field(default=True, description='发布后是否置为当前 channel 最新')
    assets: list[ReleaseAssetInput] = Field(description='各平台资产（installer + updater）')


class CiCallbackRequest(PublishReleaseRequest):
    """CI 构建完成回调（Bearer CI 密钥）——继承 publish 核心，附 build 关联"""

    build_id: int | None = Field(default=None, description='关联 release_build.id（回填状态用）')


class SetLatestRequest(SchemaBase):
    """置为最新 / 回滚入参"""

    channel: str = Field(default='stable', description='stable/beta')


class UpdateReleaseMetaRequest(SchemaBase):
    """编辑版本元数据（changelog / 状态）入参"""

    release_notes_md: str | None = None
    release_notes_en_md: str | None = None
    status: str | None = Field(default=None, description='draft/published/deprecated')


class GithubBuildRequest(SchemaBase):
    """触发 GitHub Actions 构建入参"""

    ref: str = Field(description='branch 或 tag，如 main / v1.2.0')
    channel: str = Field(default='stable', description='stable/beta')


class ReleaseDetail(SchemaBase):
    """版本详情（含资产列表）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    channel: str
    release_notes_md: str | None = None
    release_notes_en_md: str | None = None
    status: str
    is_latest: bool
    source: str
    github_run_id: str | None = None
    published_time: datetime | None = None
    created_time: datetime
    assets: list[ReleaseAssetDetail] = Field(default_factory=list)


class BuildDetail(SchemaBase):
    """构建任务详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ref: str
    channel: str
    status: str
    version: str | None = None
    github_run_id: str | None = None
    github_run_url: str | None = None
    triggered_by: str | None = None
    error_message: str | None = None
    created_time: datetime


# --------- 官网/桌面端消费用 ---------


class LatestReleaseResponse(SchemaBase):
    """官网 Hero + 下载页：当前 channel 最新版本 + 各平台安装包"""

    version: str | None = None
    channel: str = 'stable'
    published_time: datetime | None = None
    release_notes_md: str | None = None
    installers: dict[str, ReleaseAssetDetail] = Field(
        default_factory=dict, description='platform_target → installer 资产（dmg）'
    )


class TauriPlatformEntry(SchemaBase):
    """Tauri updater manifest 单平台条目"""

    signature: str = Field(description='minisign 签名')
    url: str = Field(description='updater 包（app.tar.gz）CDN url')


class TauriUpdaterManifest(SchemaBase):
    """Tauri v2 updater manifest（open 端点返回，客户端自行比对+验签）"""

    version: str
    pub_date: str | None = None
    notes: str | None = None
    platforms: dict[str, TauriPlatformEntry] = Field(default_factory=dict)
