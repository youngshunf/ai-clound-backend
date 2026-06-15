from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnEnterpriseMemberRole(Base):
    """成员与企业自定义角色 / 部门关联"""

    __tablename__ = 'hasn_enterprise_member_role'

    id: Mapped[id_key] = mapped_column(init=False)
    enterprise_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属企业 ID')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='成员 sys_user.id')
    role_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='企业角色 / 部门 ID（hasn_enterprise_role.id）')
