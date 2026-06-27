import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_design.model._base import HasnDesignAppBase
from backend.common.model import UniversalText, id_key


class HasnDesignProject(HasnDesignAppBase):
    """设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）"""

    __tablename__ = 'hasn_design_project'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键；= 设计 §5.9-2 的 owner_id 数据隔离）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目名（= OpenPencil 文档名）')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='项目说明')
    thumbnail_asset_uri: Mapped[str | None] = mapped_column(
        sa.String(512), default=None, comment='缩略图资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）'
    )
    bound_agent_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='绑定设计分身 hasn_id（BoundAgentControl，对齐 deck/studio bound_agent_id）',
    )
    canvas_meta: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='画布轻元数据 jsonb（{width,height,page_count}）'
    )
    latest_artifact_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='最近导出产物公开标识（public.hasn_artifacts.artifact_id，art_<ulid>；非硬 FK）',
    )
    enterprise_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='企业归属 id（GE 双模：个人项目为空，企业项目归企业）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='状态 (draft:草稿:blue/active:活跃:green/archived:归档:gray)'
    )
    visibility: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='可见性 (private:私有:gray/shared:已分享:blue/public:公开:green)'
    )
