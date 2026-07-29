from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class OptoutRecord(HasnGrowthAppBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）"""

    __tablename__ = 'optout_record'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='退订作用域')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业作用域 ID')
    channel: Mapped[str] = mapped_column(sa.String(24), default='', comment='渠道；all=全渠道')
    address_hash: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='旧 SHA256 兼容列，只读，禁止新写'
    )
    address_hmac: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='归一化联系方式的服务端 HMAC'
    )
    hash_key_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='HMAC 密钥版本')
    growth_project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='退订来源漏斗，仅作归因，不参与匹配'
    )
    customer_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    reason: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    source: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
