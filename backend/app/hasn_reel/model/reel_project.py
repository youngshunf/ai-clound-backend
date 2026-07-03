import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_reel.model._base import HasnReelAppBase
from backend.common.model import UniversalText, id_key


class ReelProject(HasnReelAppBase):
    """短视频项目（reel：一组创作的容器 + 默认创作参数）"""

    __tablename__ = 'reel_project'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份）')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目标题（如「秋季热饮系列」）')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='项目说明')
    settings: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='默认创作参数 jsonb（比例/单段时长/音色/素材源/字幕/调性，喂创作缺省）')
    cover_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='封面资产 hasn://asset/（取首条成片首帧；序列化边界换 CDN 签名 URL，不存直链）')
    bound_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:进行中:green/archived:已归档:gray)')
