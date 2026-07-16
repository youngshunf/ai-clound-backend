from datetime import datetime

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_project.model._base import HasnProjectAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class HasnProjectMilestone(HasnProjectAppBase):
    """平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

    里程碑是纯业务状态标记：只有 pending/done 两态 + 可空到期时间 + 可关联产物锚点，
    绝无依赖边 / 门控 / 产出闸（doc38 第四铁律）。逾期由读时按当前时间派生，不落库状态。
    """

    __tablename__ = 'hasn_project_milestone'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[str | UUID] = mapped_column(
        sa.UUID(), default=None, comment='所属项目 id（hasn_project.id，物理 FK 级联删）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='里程碑名')
    due_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='到期时间（可空；逾期由读时按当前时间派生，不落库状态）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='pending', comment='状态 (pending:待完成:blue/done:已完成:green)'
    )
    artifact_ref: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='关联产物引用（hasn:// 资源或 artifact_id，可空；业务交付节点的锚，doc38 §12.4）'
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序（里程碑轨横向次序）')
