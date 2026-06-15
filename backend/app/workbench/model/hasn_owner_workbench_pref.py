from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.workbench.model._base import WorkbenchBase
from backend.common.model import id_key


class HasnOwnerWorkbenchPref(WorkbenchBase):
    """HASN 主人工作台偏好（主脑指针 + 每日简报偏好）"""

    __tablename__ = 'hasn_owner_workbench_pref'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 hasn_id（每人一行，唯一）')
    primary_agent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='主脑分身 hasn_id（空=回落首个分身）')
    briefing_enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='每日简报开关')
    briefing_time: Mapped[str] = mapped_column(sa.String(5), default='08:00', comment='简报生成时刻（本地时区 HH:MM）')
    briefing_sources: Mapped[list] = mapped_column(
        postgresql.JSONB(),
        default_factory=lambda: ['task', 'social', 'app', 'plan'],
        comment='简报数据源开关（JSONB 数组：task/social/app/plan）',
    )
    # 应用平台 v3 P3：身份上下文瘦指针（替代退役的 hasn_user_active_workspace，设计 17 §4.2(1)）。
    # null=个人上下文；非 null=当前以该企业身份新建产物 / 看哪批列表。顶栏「当前企业」下拉切换它。
    active_enterprise_id: Mapped[int | None] = mapped_column(
        sa.BigInteger(), default=None, comment='当前企业上下文 ID（null=个人）'
    )
