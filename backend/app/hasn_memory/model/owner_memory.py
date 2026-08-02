"""Owner 记忆合并态模型。

主脑在本地整理语义事实，经合并闸提交 owner 画像后，下发给该 owner 所有 Agent 的 USER.md。
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
    # doc19 §4.6：主人手工改过正文的逃生口。下一轮画像重算的 prompt **必须携带主人手工版本
    # 并保留其改动意图**，禁止静默冲掉——保留不了就把差异显式摆给主人确认（零 fake 同款要求）。
    owner_edited: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, comment='主人是否手工改过正文（true 时下轮重算必须保留其意图）'
    )
    # doc19 §5.5：主脑单点必须可见，不许静默停摆——记忆页据此显示「上次整理于 X，主脑在 <设备> 上」。
    last_merge_run_id: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='最近一轮合并的 run_id（merge_run.run_id）'
    )
    last_merge_node_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='最近一轮合并的执行节点 node_id（主脑所在设备）'
    )
    last_merge_summary: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='最近一轮合并的结果摘要（面向主人，记忆页可见）'
    )
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
