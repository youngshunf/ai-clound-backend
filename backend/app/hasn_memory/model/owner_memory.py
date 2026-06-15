"""Owner 记忆模型（USER.md 合并线，ADR 2026-05-30）。

各 Agent 把本地 USER.md 观察上传为 contribution → owner 级合并压缩 → 下发给该 owner
所有 Agent 的 user_md。两表标准 fba 结构（bigint id + created_time/updated_time），落
`hasn_memory` schema（ADR-15 收编：原 public.hasn_owner_memory[_contribution] 去前缀搬迁）。
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_memory.model._base import HasnMemoryBase
from backend.common.model import TimeZone, UniversalText, id_key


class HasnOwnerMemory(HasnMemoryBase):
    """Owner 记忆（权威，owner 维度）。

    跨该 owner 所有 Agent 的 USER.md 观察合并压缩后的结果，作为下发给每个
    Agent 的 user_md 的事实源（ADR 2026-05-30 §2/§4）。
    """

    __tablename__ = 'owner_memory'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(
        sa.String(40), default='', unique=True, comment='Owner 的 hasn_id（hasn_humans.hasn_id）'
    )
    content: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='合并压缩后的 USER.md（下发给各 Agent）'
    )
    version: Mapped[int] = mapped_column(sa.Integer, default=1, comment='记忆版本（每次合并 +1）')
    token_count: Mapped[int | None] = mapped_column(sa.Integer, default=None, comment='压缩后内容估算 token 数')
    last_merged_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后合并时间')
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明


class HasnOwnerMemoryContribution(HasnMemoryBase):
    """Owner 记忆贡献（各 Agent 上传，待合并）。"""

    __tablename__ = 'owner_memory_contribution'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner 的 hasn_id')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上传 Agent 的 hasn_id')
    content: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='Agent 观察到的主人信息片段（本地 USER.md 增量）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(20),
        default='pending',
        comment='状态 (pending:待合并:orange/merged:已合并:green/discarded:丢弃:gray)',
    )
    merged_into_version: Mapped[int | None] = mapped_column(
        sa.Integer, default=None, comment='合并进的 owner_memory 版本'
    )
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
