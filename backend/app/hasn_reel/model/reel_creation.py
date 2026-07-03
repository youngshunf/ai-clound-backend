from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_reel.model._base import HasnReelAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class ReelCreation(HasnReelAppBase):
    """一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）"""

    __tablename__ = 'reel_creation'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属项目 id（FK→reel_project）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='编排分身 hasn_id（分身路径；AgentIdentity 展示）')
    title: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='创作标题（可从 idea 派生）')
    idea: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='主人需求原话（派发输入快照）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='发起方式 (user_pipeline:一键流水线:blue/agent_pipeline:分身代发起流水线:geekblue/agent_tools:分身工具编排:purple)')
    session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='工作会话 id（分身路径——分身工作流步骤/产物在工作会话事件流，续接锚点 AC-P2）')
    engine_task_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地 MPT 引擎任务 id（流水线路径——进度来源；reel 引擎本地 sidecar）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待开始:default/running:进行中:processing/waiting_user:等你回答:gold/succeeded:已完成:green/failed:失败:red)')
    stage: Mapped[str | None] = mapped_column(sa.String(120), default=None, comment='当前阶段文本（脚本/配音/字幕/素材/合成；透传 MPT 或会话推进）')
    progress: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='进度 0-100')
    video_ref: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='成片引用 jsonb（本地优先 {kind:local,path,node_id,uploaded} 或上云后 {kind:asset,uri:hasn://asset/...}）')
    thumbnail_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='首帧/缩略图 hasn://asset/')
    duration_sec: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='成片时长（秒）')
    resolution: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='成片分辨率（如 1080x1920）')
    result_refs: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='中间产物引用 jsonb（文案/音频/字幕/素材；细节明细复用工作会话 session_artifacts）')
    error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='失败真实错误（透传引擎，零 fake）')
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='结束时间')
