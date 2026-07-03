import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_diag.model._base import HasnDiagAppBase
from backend.common.model import UniversalText, id_key


class ErrorIssueEvent(HasnDiagAppBase):
    """错误问题处理动作审计流水（谁在何时把状态改成什么）"""

    __tablename__ = 'error_issue_event'

    id: Mapped[id_key] = mapped_column(init=False)
    fingerprint: Mapped[str] = mapped_column(sa.String(64), default='', comment='归类键')
    actor_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='操作分身 hasn_id；自动动作=system:auto-reopen'
    )
    from_status: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='原状态（可空：首次创建）')
    to_status: Mapped[str] = mapped_column(sa.String(16), default='', comment='新状态')
    note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='留言 / 重开原因')
