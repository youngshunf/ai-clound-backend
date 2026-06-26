from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key


class CrawledUrl(HasnGrowthAppBase):
    """URL 级去重登记表（平台级·抓前查重防重复爬·doc08 §4.4）"""

    __tablename__ = 'crawled_url'

    id: Mapped[id_key] = mapped_column(init=False)
    url_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='规范化 URL 的 sha256（唯一去重键）')
    normalized_url: Mapped[str] = mapped_column(
        sa.String(2048), default='', comment='规范化 URL（小写host/去www/去fragment/去追踪参数/query排序）'
    )
    domain: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='域名（按域统计/限频）')
    source_type: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='首次抓取的数据源类型')
    crawl_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='实际抓取次数（过期重抓累加）')
    hit_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='抓前查重命中次数（被跳过次数·省成本度量）')
    lead_yield: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='该 URL 累计产出有效线索数')
    last_outcome: Mapped[str] = mapped_column(
        sa.String(16),
        default='pending',
        comment='最近抓取结果 (pending:待抓:gray/succeeded:成功:green/empty:无内容:orange/failed:失败:red)',
    )
    last_job_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='最近抓取所属采集任务 id')
    first_crawled_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    last_crawled_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    meta_data: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
