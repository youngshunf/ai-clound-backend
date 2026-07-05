from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_release.model._base import HasnReleaseAppBase
from backend.common.model import id_key, UniversalText


class ReleaseBuild(HasnReleaseAppBase):
    """CI 构建任务（GitHub Actions 构建进度追踪）"""

    __tablename__ = 'release_build'

    id: Mapped[id_key] = mapped_column(init=False)
    ref: Mapped[str] = mapped_column(sa.String(128), default='', comment='构建 ref（branch 或 tag，如 main / v1.2.0）')
    channel: Mapped[str] = mapped_column(sa.String(16), default='', comment='目标渠道 (stable:稳定版:green/beta:内测版:orange)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='构建状态 (queued:排队中:gray/building:构建中:blue/success:成功:green/failed:失败:red)')
    version: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='产出版本号（构建成功后回填）')
    github_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='GitHub Actions run id')
    github_run_url: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='GitHub Actions run 页面链接')
    triggered_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='触发者（管理员用户名/hasn_id）')
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='失败原因（status=failed 时）')
