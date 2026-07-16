from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnProject(Base):
    """平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）"""

    __tablename__ = 'hasn_project'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，逻辑引用 public.hasn_humans，绝不跨 owner）')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目名')
    goal: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='一句话目标（分身建项目时采集，供聚合视图与派发上下文注入，可空）')
    cover_asset_uri: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='封面图资产引用（hasn://asset/{id}，来源=上传/素材下载/AI 生成；序列化边界换 CDN 签名 URL，不存直链；可空回落品牌渐变+首字）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:进行中:blue/archived:已归档:gray)')
    bound_agent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='默认协作分身 HASN ID（owner 名下 a_* 分身，null=未绑定；对齐 doc21 AppCollab，列名铁律 doc38 §8）')
    enterprise_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='企业归属（双模化，个人 NULL / 企业非空，对齐 GE，可空）')
