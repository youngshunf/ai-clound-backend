import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class IndustryTag(HasnGrowthAppBase):
    """行业标准化标签字典（公共池检索按 code 归一·doc08 §4.3）"""

    __tablename__ = 'industry_tag'

    id: Mapped[id_key] = mapped_column(init=False)
    code: Mapped[str] = mapped_column(sa.String(64), default='', comment='标准行业标签（稳定标识，公共池按此归一检索）')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment='行业中文名')
    aliases: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='别名数组（原始行业文本命中任一别名即归一到本 code）'
    )
    parent_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='父级行业 code（层级，可空；初版扁平）')
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    meta_data: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
