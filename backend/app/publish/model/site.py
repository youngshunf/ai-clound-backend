"""已发布制品根表（webpublish.site）。"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.publish.model._base import PublishAppBase
from backend.common.model import TimeZone, UniversalText, id_key

KIND_COMMENT = (
    '制品类型 (deck:演示文稿:violet/report:报告:blue/page:单页:green/dashboard:看板:orange/other:其它:gray)'
)
STATUS_COMMENT = '状态 (active:生效:green/revoked:已撤销:gray)'
VISIBILITY_COMMENT = '可见性 (private:私有:gray/password:口令:orange/unlisted:不公开:blue/public:公开:green)'


class Site(PublishAppBase):
    """已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）"""

    __tablename__ = 'site'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发布者 owner HASN ID（owner 隔离键）')
    publisher_agent_id: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='若由 agent 代发布，记发起分身 HASN ID（审计，可空）'
    )
    kind: Mapped[str] = mapped_column(sa.String(32), default='page', comment=KIND_COMMENT)
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='展示标题')
    slug: Mapped[str] = mapped_column(sa.String(32), default='', comment='不可枚举短码，分享路径 /s/{slug}')
    source_app: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='来源应用（deck 等，可空）')
    source_ref: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='来源实体 id（如 deck_id，可空）')
    current_revision_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='当前对外版本指针（引用 webpublish.revision.id，可空）'
    )
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment=STATUS_COMMENT)
    visibility: Mapped[str] = mapped_column(sa.String(16), default='private', comment=VISIBILITY_COMMENT)
    password_hash: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='visibility=password 时存 bcrypt hash（绝不存明文，可空）'
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='过期即拒访（含 unlisted/public，可空）'
    )
    allow_present: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否允许放映/演讲者模式')
    allow_download: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否允许下载原始制品')
    allow_indexing: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='public 时是否允许公开收录（默认不收录；unlisted 恒 noindex）'
    )
    view_count: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='访问计数（统计，非鉴权）')
    rev: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='元数据乐观锁/同步游标（每次写 +1）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')
