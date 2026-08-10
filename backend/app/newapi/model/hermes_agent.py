"""`hermes_agent` 表模型（2026-08-10 自 `app/hermes` 迁入 `app/newapi`）。

云端 Runtime 形态已退役（云端改为部署无头 hasn-node），原 `app/hermes` 模块整体删除。
但本表承载 **per-agent 用量归属**：`app/newapi` 的用户端用量接口按 `agent_id` 反查 `user_id`，
属于计费/用量域而非 Runtime 编排域，故随唯一存活消费方迁到 `app/newapi`。

⚠️ `__tablename__` 保持 `hermes_agent` 不变——迁移只换代码归属，不动物理表，零 migration。
"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HermesAgent(Base):
    """Hermes Agent 表"""

    __tablename__ = 'hermes_agent'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 业务 ID')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    agent_name: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 名称')
    template: Mapped[str] = mapped_column(sa.String(32), default='', comment='模板 (assistant:通用助理:blue/office:办公助理:cyan/creator:内容创作:purple/custom:自定义:gray)')
    timezone: Mapped[str] = mapped_column(sa.String(64), default='', comment='时区')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Agent状态 (creating:创建中:orange/created:已创建:blue/ready:就绪:cyan/running:运行中:green/stopped:已停止:gray/error:异常:red/deleting:删除中:orange/deleted:已删除:gray)')
    runtime_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='Runtime 实例 ID')
    runtime_profile_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Runtime Profile ID')
    profile_name: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Hermes profile 名')
    llm_mode: Mapped[str] = mapped_column(sa.String(16), default='', comment='LLM模式 (platform:平台托管:green/byok:用户自带:blue)')
    llm_provider: Mapped[str] = mapped_column(sa.String(32), default='', comment='LLM Provider')
    llm_model: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='LLM 模型')
    gateway_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Gateway状态 (stopped:已停止:gray/starting:启动中:orange/running:运行中:green/restarting:重启中:orange/stopping:停止中:orange/unhealthy:不健康:red/error:异常:red)')
    workspace_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Workspace状态 (unknown:未知:gray/ready:就绪:green/active:运行中:blue/error:异常:red)')
    sandbox_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='Sandbox状态 (unknown:未知:gray/ready:就绪:green/unprotected:未保护:orange/error:异常:red)')
    channel_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已绑定渠道数')
    last_active_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近活跃时间')
    last_runtime_sync_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近 Runtime 同步时间')
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近错误码')
    last_error_message: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='最近错误说明')
    remark: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='备注')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删除时间')
