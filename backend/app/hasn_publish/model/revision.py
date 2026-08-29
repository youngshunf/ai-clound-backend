"""制品不可变版本表（hasn_publish.revision）。"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_publish.model._base import PublishAppBase
from backend.common.model import TimeZone, id_key

RUNTIME_COMMENT = '运行时形态 (single-html:单文件:green/bundle-zip:含资产:blue)'


class Revision(PublishAppBase):
    """制品不可变版本（指向私有桶制品对象，URL 不变可回滚）"""

    __tablename__ = 'revision'

    id: Mapped[id_key] = mapped_column(init=False)
    site_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 site（引用 hasn_publish.site.id，bigint）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID（owner 隔离键）')
    seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='版本序号（site 内递增；(site_id, seq) 唯一）')
    asset_id: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='制品在 public.hasn_assets 的 id（access=private）'
    )
    runtime: Mapped[str] = mapped_column(sa.String(16), default='single-html', comment=RUNTIME_COMMENT)
    content_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='制品内容哈希 sha256（去重/校验/幂等）')
    size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='制品大小（字节）')
    manifest_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='bundle-zip 子文件清单（name→object_key/mime/size）；single-html 为 null'
    )
    # 发布异步化（2026-08-29）：bundle-zip 物化（读 zip + 逐对象 PUT 对象存储，实测 38s+）挪进
    # Celery，请求内只落 pending revision 立即返回。存量行 DEFAULT 'ready' 回填即真相。
    materialize_status: Mapped[str] = mapped_column(
        sa.String(16), default='ready', comment='物化状态（ready:已物化/pending:物化在途/failed:物化失败；仅 bundle-zip 会出现非 ready）'
    )
    materialize_error: Mapped[str | None] = mapped_column(
        sa.Text(), default=None, comment='物化失败的主人可读原因（materialize_status=failed 时非空）'
    )
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删/回收）')
