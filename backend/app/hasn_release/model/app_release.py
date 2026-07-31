from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_release.model._base import HasnReleaseAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class AppRelease(HasnReleaseAppBase):
    """桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）"""

    __tablename__ = 'app_release'

    id: Mapped[id_key] = mapped_column(init=False)
    version: Mapped[str] = mapped_column(sa.String(32), default='', comment='semver 版本号，如 1.2.0')
    channel: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='发布渠道 (stable:稳定版:green/beta:内测版:orange)'
    )
    release_notes_md: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='更新日志 Markdown（中）')
    release_notes_en_md: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='更新日志 Markdown（英）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='状态 (draft:草稿:gray/published:已发布:green/deprecated:已下线:slate)'
    )
    is_latest: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=True, comment='是否当前 channel 最新（置新版时把同 channel 旧版落 false）'
    )
    source: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='来源 (manual:手动上传:blue/github:GitHub构建:purple)'
    )
    github_run_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='关联 GitHub Actions run id（source=github 时）'
    )
    release_tag: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='云端锁定的 Git release tag，如 v0.3.1'
    )
    previous_release_tag: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='生成更新说明时使用的上一个真实 Git release tag'
    )
    source_commit: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='release tag 锁定的 hasn-node Git commit'
    )
    tag_status: Mapped[str] = mapped_column(
        sa.String(16), default='not_required', comment='tag 状态 (not_required:旧流程/pending:待推送/ready:已核验)'
    )
    tag_created_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='release tag 经云端核验的时间'
    )
    required_platforms: Mapped[list[str]] = mapped_column(
        JSONB, default_factory=list, comment='正式发布要求的平台目标 JSON 数组'
    )
    completed_platforms: Mapped[list[str]] = mapped_column(
        JSONB, default_factory=list, comment='installer 与 updater 均已上传的平台目标 JSON 数组'
    )
    release_commits: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, default_factory=list, comment='上一个 release tag 到本次 tag 的 Git 提交摘要'
    )
    release_notes_status: Mapped[str] = mapped_column(
        sa.String(16),
        default='manual',
        comment='更新说明状态 (manual:人工/pending:待生成/ready:已生成/failed:生成失败)',
    )
    release_notes_error: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='LLM 更新说明生成失败原因'
    )
    published_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='发布时刻（status 转 published 时写入）'
    )
    # 无头 hasn-node 托管（H8）：主后端发布不兼容变更前，先确认在线云端节点已滚动到该契约版本以上。
    min_cloud_contract_version: Mapped[str | None] = mapped_column(
        sa.String(32), default=None, comment='本版要求的最低云端契约版本（NULL=不设门槛）'
    )
