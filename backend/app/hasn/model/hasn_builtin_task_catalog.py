from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnBuiltinTaskCatalog(Base):
    """HASN 官方内置任务目录（云端权威）"""

    __tablename__ = 'hasn_builtin_task_catalog'

    id: Mapped[id_key] = mapped_column(init=False)
    builtin_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='内置任务全局唯一键（如 daily_briefing）')
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='任务名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='任务说明')
    schedule_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='调度类型 (cron:Cron表达式:blue/interval:间隔:green/once:单次:gray)')
    schedule_config: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='调度配置 JSONB（cron 用 {"expr":"0 8 * * *"}）')
    skill_bundle: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='执行技能包（如 huanxing/workbench-briefing）')
    system_prompt: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='系统提示词（约束输出统一格式）')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='全局上/下线开关')
    min_node_version: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='要求的最低客户端版本（可空）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='目录版本号（变化时 daemon 重拉）')
