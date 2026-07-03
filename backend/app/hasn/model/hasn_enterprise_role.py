import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnEnterpriseRole(Base):
    """企业自定义角色 / 部门"""

    __tablename__ = 'hasn_enterprise_role'

    id: Mapped[id_key] = mapped_column(init=False)
    enterprise_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属企业 ID')
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='角色 / 部门名称（如「销售部」「财务」）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='类型 (role:角色:blue/department:部门:green)')
