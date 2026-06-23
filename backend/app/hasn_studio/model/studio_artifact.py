from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_studio.model._base import HasnStudioAppBase
from backend.common.model import id_key


class StudioArtifact(HasnStudioAppBase):
    """视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）"""

    __tablename__ = 'studio_artifact'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属项目 id（FK→studio_project）')
    render_job_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='产出该成品的渲染 job id（FK→studio_render_job；可空兼容外部导入）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='协作分身 hasn_id（AgentIdentity 展示）')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='成品标题')
    pipeline_key: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='用了哪条管线 key（成品卡展示）')
    video_asset_uri: Mapped[str] = mapped_column(sa.String(512), default='', comment='成片 mp4 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    thumbnail_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='缩略图/首帧 hasn://asset/')
    duration_sec: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='时长（秒）')
    resolution: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='分辨率（如 1080x1920）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (generating:生成中:blue/reviewing:待审核:gold/completed:已完成:green/failed:失败:red)')
    origin_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='续接归属来源 (app:应用:blue)（AC-P2）')
    active_work_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='「改一版」回到同一工作会话 id（AC-P2）')
    meta: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='成品元数据 jsonb（provider 用量/成本/分镜数）')
