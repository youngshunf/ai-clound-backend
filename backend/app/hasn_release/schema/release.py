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
# 无头 hasn-node 容器镜像目标（H8）：与桌面 target 同表不同语义——不是可下载文件，
# download_url 存 registry ref、sha256 存镜像 digest（契约 §1 / §7）。
HEADLESS_PLATFORM_TARGETS = ('headless-linux-amd64', 'headless-linux-arm64')
ASSET_KINDS = ('installer', 'updater', 'image')
CHANNELS = ('stable', 'beta')
REQUIRED_DESKTOP_PLATFORMS = (
    'darwin-aarch64',
    'darwin-x86_64',
    'windows-x86_64',
    'linux-x86_64',
)


class HeadlessImageRequest(SchemaBase):
    """无头镜像登记入参（CI `release-headless-node.sh` 推私有 registry 后回调）。

    只加不改：本请求**只**upsert 一条 `asset_kind='image'` 资产，不动 `is_latest`、不动
    桌面端资产、不改发布批次状态——桌面发布链路行为完全不变。
    """

    version: str = Field(description='semver，与桌面端同版本号')
    channel: str = Field(default='stable', description='stable/beta')
    platform_target: str = Field(description='headless-linux-amd64 / headless-linux-arm64')
    image_ref: str = Field(description='私有 registry ref，如 registry.example.com/hasn-node:1.2.0')
    image_digest: str = Field(description='镜像 digest，形如 sha256:<64hex>（以 digest 为准，不信 tag）')
    image_size: int = Field(default=0, description='镜像字节数（可选）')
    release_notes_md: str | None = Field(
        default=None,
        description='发布说明（Markdown）。设计 §8.1 要求登记内容含 changelog；'
        '仅在该版本尚无发布说明时写入，绝不覆盖桌面端已维护的正文',
    )
    min_cloud_contract_version: str | None = Field(default=None, description='本版要求的最低云端契约版本')
    publish: bool = Field(default=True, description='是否把该版本置为 published（仅在其尚未发布时生效）')


class HeadlessImageDetail(SchemaBase):
    """无头镜像登记结果。"""

    release_id: int
    version: str
    channel: str
    status: str
    platform_target: str
    image_ref: str
    image_digest: str
    min_cloud_contract_version: str | None = None
    release_notes_written: bool = Field(
        default=False,
        description='本次是否真的写入了发布说明。'
        '写入语义是「只填空缺不覆盖」，该版本已有正文时本次会被跳过——'
        '回显这一位，调用方才能如实报告结果而不是声称「已登记」',
    )


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
    release_id: int | None = Field(default=None, description='云端发布批次 app_release.id')
    release_tag: str | None = Field(default=None, description='云端锁定的 release tag')


class PrepareReleaseRequest(SchemaBase):
    """创建或加入当前桌面端发布批次。"""

    channel: str = Field(default='stable', description='stable/beta')
    source_commit: str = Field(
        min_length=40,
        max_length=64,
        pattern=r'^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$',
        description='第一台机器看到的 origin/main Git commit',
    )


class ReleaseCommitInput(SchemaBase):
    """用于生成版本说明的单条 Git 提交。"""

    sha: str = Field(min_length=7, max_length=64, pattern=r'^[0-9a-fA-F]+$')
    subject: str = Field(min_length=1, max_length=500)


class ConfirmReleaseTagRequest(SchemaBase):
    """确认远端 release tag 并提交该版本的 Git 历史。"""

    source_commit: str = Field(
        min_length=40,
        max_length=64,
        pattern=r'^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$',
    )
    previous_release_tag: str | None = Field(default=None, max_length=64)
    commits: list[ReleaseCommitInput] = Field(default_factory=list, max_length=5000)


class ReleaseBatchResponse(SchemaBase):
    """跨平台发布批次状态。"""

    id: int
    version: str
    channel: str
    release_tag: str
    previous_release_tag: str | None = None
    source_commit: str
    tag_status: str
    release_notes_status: str
    release_notes_md: str | None = None
    release_notes_error: str | None = None
    required_platforms: list[str] = Field(default_factory=list)
    completed_platforms: list[str] = Field(default_factory=list)
    status: str
    published_time: datetime | None = None


class CiUploadResponse(SchemaBase):
    """CI 上传产物到公共桶的返回（供 CI 组装 ReleaseAssetInput）。

    复用云端既有七牛公共桶：CI 出包后把二进制交云端此处入桶，不再需要任何七牛凭据。
    """

    download_url: str = Field(description='公共桶 CDN https 直链（长效不签名）')
    file_name: str = Field(description='文件名（已取 basename 防穿越）')
    file_size: int = Field(description='字节数')
    sha256: str = Field(description='服务端据落桶字节算得的 sha256（供 CI 与本地校验对拍）')
    object_key: str = Field(description='对象存储 key（溯源用）')


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
    release_tag: str | None = None
    previous_release_tag: str | None = None
    source_commit: str | None = None
    tag_status: str = 'not_required'
    tag_created_time: datetime | None = None
    required_platforms: list[str] = Field(default_factory=list)
    completed_platforms: list[str] = Field(default_factory=list)
    release_notes_status: str = 'manual'
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
    """官网 Hero + 下载页：当前最高版本 + 各平台自己的最新安装包"""

    version: str | None = None
    channel: str = 'stable'
    published_time: datetime | None = None
    release_notes_md: str | None = None
    platform_versions: dict[str, str] = Field(default_factory=dict, description='platform_target → 该安装包所属版本')
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
