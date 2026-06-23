from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_studio.model._base import HasnStudioAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class StudioRenderJob(HasnStudioAppBase):
    """视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）"""

    __tablename__ = 'studio_render_job'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属项目 id（FK→studio_project）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='发起分身 hasn_id')
    pipeline_key: Mapped[str] = mapped_column(sa.String(80), default='', comment='跑哪条管线 key（run_pipeline，来自引擎 pipeline_defs）')
    input: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='入参快照 jsonb（脚本/素材引用/参数，不回指项目当前值）')
    engine_job_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='引擎侧 job 标识（云端轮询/回流用）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (queued:排队中:blue/running:执行中:processing/succeeded:成功:green/failed:失败:red/canceled:已取消:default)')
    progress: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='进度 0-100')
    stage: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='当前阶段文本（生成第3镜/配音/合成，喂 CRX-1 进度抽屉）')
    cost: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='成本计量 jsonb（{gpu_sec, provider_usage, credits}，按 job 计量并入计费）')
    work_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='触发的工作会话 id（续接锚点 AC-P2）')
    error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='失败真实错误（透传引擎，零 fake）')
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='结束时间')
