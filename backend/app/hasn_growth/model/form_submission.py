import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class FormSubmission(HasnGrowthAppBase):
    """获客落地页表单回流（inbound 线索缓冲区）"""

    __tablename__ = 'form_submission'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    publish_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    email: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    phone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    company: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待处理:gray/converted:已转化:green/rejected:已拒绝:red/spam:垃圾:red)')
    customer_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_meta: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='UTM/referrer/IP hash（反滥用 + 归因）')
    # 企业化双模归属（GE1，设计 v3 §6.7）：inbound 留资归企业池/分配。
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）')
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式）')
