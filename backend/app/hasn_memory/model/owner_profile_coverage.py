"""主人画像完整度模型（hasn_memory.owner_profile_coverage）。

「了解主人」功能的结构化判定层：owner × dimension 一行，分身对主人 5 个画像维度各自的
覆盖状态 + LLM 打分。驱动首页「让分身了解你」入口显隐、采访"缺什么采访什么"、主动规划闭环开关。
落 `hasn_memory` schema（继承 HasnMemoryBase）。
设计事实源：docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §4.1
"""

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_memory.model._base import HasnMemoryBase
from backend.common.model import TimeZone, UniversalText, id_key


class OwnerProfileCoverage(HasnMemoryBase):
    """主人画像完整度（云端权威，owner×维度）。"""

    __tablename__ = 'owner_profile_coverage'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='主人 hasn_id（hasn_humans.hasn_id）'
    )
    dimension: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='画像维度 (interests:兴趣爱好:blue/work:工作情况:cyan/residence:居住地址:green/goals:近期目标:orange/life_plan:人生规划:purple)',
    )
    status: Mapped[str] = mapped_column(
        sa.String(20),
        default='missing',
        comment='覆盖状态 (missing:缺失:gray/partial:部分:orange/sufficient:充分:green)',
    )
    confidence: Mapped[Decimal] = mapped_column(
        sa.Numeric(4, 3), default=Decimal(0), comment='LLM 打分置信度 0–1'
    )
    summary: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='该维度已知信息的一句话摘要（面向主人；驱动入口卡"已了解"）'
    )
    missing_hint: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='该维度还缺什么（驱动采访提问 + 入口卡"还需了解"）'
    )
    evidence_version: Mapped[int] = mapped_column(
        sa.Integer, default=0, comment='判定所依据的 owner_memory.version（防陈旧重判）'
    )
    assessed_time: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='最近一次判定时间'
    )
    # created_time / updated_time 由 Base(DateTimeMixin) 提供，勿重复声明
