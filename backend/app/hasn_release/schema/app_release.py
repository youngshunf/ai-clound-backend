from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class AppReleaseSchemaBase(SchemaBase):
    """桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）基础模型"""
    version: str = Field(description='semver 版本号，如 1.2.0')
    channel: str = Field(description='发布渠道 (stable:稳定版:green/beta:内测版:orange)')
    release_notes_md: str | None = Field(None, description='更新日志 Markdown（中）')
    release_notes_en_md: str | None = Field(None, description='更新日志 Markdown（英）')
    status: str = Field(description='状态 (draft:草稿:gray/published:已发布:green/deprecated:已下线:slate)')
    is_latest: bool = Field(description='是否当前 channel 最新（置新版时把同 channel 旧版落 false）')
    source: str = Field(description='来源 (manual:手动上传:blue/github:GitHub构建:purple)')
    github_run_id: str | None = Field(None, description='关联 GitHub Actions run id（source=github 时）')
    published_time: datetime | None = Field(None, description='发布时刻（status 转 published 时写入）')


class CreateAppReleaseParam(AppReleaseSchemaBase):
    """创建桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）参数"""


class UpdateAppReleaseParam(AppReleaseSchemaBase):
    """更新桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）参数"""


class DeleteAppReleaseParam(SchemaBase):
    """删除桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）参数"""

    pks: list[int] = Field(description='桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针） ID 列表')


class GetAppReleaseDetail(AppReleaseSchemaBase):
    """桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
