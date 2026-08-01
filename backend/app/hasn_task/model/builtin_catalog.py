"""官方内置任务目录模型（hasn_task.builtin_catalog，原 public.hasn_builtin_task_catalog）。"""

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import UniversalText, id_key

# 广播语义（doc19 §9 / D-24）：master_brain=按 target_agent_type 绑单个分身（NULL 回退主脑，既有语义不变）；
# all_agents=本节点每个在线分身各执行一次（云端仍只播一行 task，扇出在本地 task_scheduler）。
TARGET_SCOPE_COMMENT = (
    '广播语义 (master_brain:绑单个分身:gray/all_agents:每个在线分身各一次:violet)：'
    'master_brain=按 target_agent_type 绑单个分身（NULL 回退主脑，既有语义不变）；'
    'all_agents=本节点每个在线分身各执行一次（云端仍只播一行 task，扇出在本地 task_scheduler）'
)


class HasnBuiltinTaskCatalog(HasnTaskAppBase):
    """HASN 官方内置任务目录（云端权威）"""

    __tablename__ = 'builtin_catalog'

    id: Mapped[id_key] = mapped_column(init=False)
    builtin_key: Mapped[str] = mapped_column(
        sa.String(64),
        default='',
        comment='内置任务全局唯一键（如 daily_briefing）',
    )
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='任务名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='任务说明')
    schedule_type: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='调度类型 (cron:Cron表达式:blue/interval:间隔:green/once:单次:gray)'
    )
    schedule_config: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='调度配置 JSONB（cron 用 {"expr":"0 8 * * *"}）'
    )
    skill_bundle: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='执行技能包（如 huanxing/workbench-briefing）'
    )
    system_prompt: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='系统提示词（约束输出统一格式）',
    )
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='全局上/下线开关')
    default_enabled: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=True, comment='播种时默认启用态（false=需用户手动开启）'
    )
    target_agent_type: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='承接该任务的内置 agent 类型键(builtin_key)；NULL 表示绑定主脑',
    )
    # doc19 §9 / D-24：广播语义。master_brain 沿用「按 target_agent_type 绑单个分身（NULL 回退主脑）」；
    # all_agents 表示本节点每个在线分身各执行一次——云端受 uq_task_owner_builtin_key 唯一索引约束仍只播
    # 一行 task，扇出由本地 task_scheduler 完成。
    target_scope: Mapped[str] = mapped_column(
        sa.String(16),
        default='master_brain',
        comment=TARGET_SCOPE_COMMENT,
    )
    min_node_version: Mapped[str | None] = mapped_column(
        sa.String(32),
        default=None,
        comment='要求的最低客户端版本（可空）',
    )
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='目录版本号（变化时 daemon 重拉）')
