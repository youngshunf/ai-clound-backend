import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_studio.model._base import HasnStudioAppBase
from backend.common.model import UniversalText, id_key


class StudioProject(HasnStudioAppBase):
    """视频项目（统一视频引擎 studio：管线/素材/成品的容器）"""

    __tablename__ = 'studio_project'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目标题')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='项目说明')
    default_pipeline_key: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='项目默认管线 key（来自引擎 pipeline_defs，非 PG 表）')
    settings: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='项目设置 jsonb（调性/分辨率/默认参数/品牌，喂 run_pipeline 缺省）')
    cover_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='封面资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    bound_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (draft:草稿:blue/active:进行中:green/archived:已归档:gray)')
