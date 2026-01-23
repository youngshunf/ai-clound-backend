from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.common.model import Base, id_key


class Industry(Base):
    """行业表"""

    __tablename__ = 'sys_industry'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(String(50), comment='行业名称')
    parent_id: Mapped[int | None] = mapped_column(ForeignKey('sys_industry.id'), default=None, comment='父级ID')
    keywords: Mapped[list[str] | None] = mapped_column(JSONB, default=None, comment='行业关键词')
    description: Mapped[str | None] = mapped_column(Text, default=None, comment='描述')
    sort: Mapped[int] = mapped_column(default=0, comment='排序')

    # 关联关系
    parent: Mapped['Industry'] = relationship('Industry', remote_side=[id], back_populates='children', init=False, repr=False)
    children: Mapped[list['Industry']] = relationship('Industry', back_populates='parent', init=False, repr=False)
