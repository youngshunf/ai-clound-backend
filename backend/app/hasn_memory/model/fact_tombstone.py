"""事实删除凭证兼墓碑（hasn_memory.fact_tombstone，doc19 §4.5）。

主人发起硬删（purge）后各处只留这一条凭证：`fact_id` + 删除时间 + 发起人，**绝不存被删
事实的任何内容**——否则「不留任何内容」是空话。凭证同时兼作 tombstone：origin 节点若在
purge 时离线、outbox 里还积压着该 fact 的事件，重新上线推送时云端按本表永久拒绝（一次性
warn）并回令来源节点清理本地行与残留 op。

落 `hasn_memory` schema（继承 HasnMemoryBase）；主键是被删事实自身的 `fact_id`，不用 fba
自增 id——墓碑的唯一性判据就是「这个 fact_id 已被永久删除」。
设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §4.5
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_memory.model._base import HasnMemoryBase
from backend.common.model import TimeZone, UniversalText
from backend.utils.timezone import timezone


class FactTombstone(HasnMemoryBase):
    """HASN 记忆系统 - 事实删除凭证（墓碑，绝不存被删内容）。"""

    __tablename__ = 'fact_tombstone'

    fact_id: Mapped[str] = mapped_column(
        sa.String(40), primary_key=True, default='', comment='被物理删除的事实 ID（主键）'
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 hasn_id（hasn_humans.hasn_id）')
    purged_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='物理删除执行时间')
    purged_by: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起人 hasn_id（purge 只有主人可发起）')
    cascade_from: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='级联来源 fact_id（因血缘级联被删时指向源事实；直接删除为空）'
    )
    reason: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='删除原因（面向主人的说明，不含被删事实内容）'
    )
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
