import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class TestUser(Base):
    """测试用户表"""

    __tablename__ = 'test_user'

    id: Mapped[id_key] = mapped_column(init=False)
    username: Mapped[str] = mapped_column(sa.String(64), default='', comment='用户名')
    email: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='邮箱地址')
    status: Mapped[int | None] = mapped_column(sa.SMALLINT(), default=None, comment='状态')
    user_type: Mapped[int | None] = mapped_column(sa.SMALLINT(), default=None, comment='用户类型')
