"""合并待办（hasn_memory.merge_request，doc19 §5.5）。

非主脑分身整理完自己那片后可请求合并；请求投递给主脑所在节点。**主脑离线时落云端每 owner
待办**——「去重只留最新一条，不堆积」在结构上钉死：`owner_id` 即主键，重复请求走 upsert 覆盖，
天然不排队。主脑上线后由 `task_scheduler` 触发时顺带消化（写 `consumed_time`）；待办滞留时长
计入 §5.5 的「超过阈值未成功合并」提示。

设计事实源：docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md §5.5
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_memory.model._base import HasnMemoryBase
from backend.common.model import TimeZone
from backend.utils.timezone import timezone


class MergeRequest(HasnMemoryBase):
    """HASN 记忆系统 - 合并待办（每主人至多一条，主键去重不堆积）。"""

    __tablename__ = 'merge_request'

    owner_id: Mapped[str] = mapped_column(
        sa.String(40),
        primary_key=True,
        default='',
        comment='主人 hasn_id（主键：每主人至多一条待办，重复请求覆盖）',
    )
    requested_time: Mapped[datetime] = mapped_column(
        TimeZone, default_factory=timezone.now, comment='最近一次请求时间（滞留时长 = now - 本值）'
    )
    requested_by_agent: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='发起请求的分身 hasn_id（非主脑分身）'
    )
    requested_by_node: Mapped[str] = mapped_column(sa.String(64), default='', comment='发起请求的节点 node_id')
    reason: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='请求原因（local_review_done / owner_manual 等）'
    )
    consumed_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='被主脑消化的时间（NULL = 待办仍在）'
    )
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
