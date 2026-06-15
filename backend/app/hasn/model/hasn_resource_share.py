from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnResourceShare(Base):
    """通用产物共享表（平台级显式协作授权）"""

    __tablename__ = 'hasn_resource_share'

    id: Mapped[id_key] = mapped_column(init=False)
    resource_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='产物类型 (deck:演示文稿:blue/doc:文档:cyan/knowledge:知识库:purple)')
    resource_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='产物主键（deck 为 bigint 文本）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='产物归属者 hasn_id（冗余，便于「我共享出去的」反查）')
    grantee_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='被授权对象类型 (human:人:blue/agent:分身:purple/enterprise:企业:cyan/role:角色:orange/link:链接:gray)')
    grantee_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='被授权对象 ID（human/agent=hasn_id；enterprise=enterprise_id；role=builtin:xxx 或 role.id；link=null）')
    permission: Mapped[str] = mapped_column(sa.String(16), default='', comment='权限档 (viewer:查看:gray/editor:编辑:blue/manager:管理:green)')
    granted_by: Mapped[str] = mapped_column(sa.String(64), default='', comment='授权操作者 hasn_id')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='授权状态 (active:生效:green/revoked:已撤销:red)')
