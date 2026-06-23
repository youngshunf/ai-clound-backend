import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_studio.model._base import HasnStudioAppBase
from backend.common.model import id_key


class StudioAsset(HasnStudioAppBase):
    """视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）"""

    __tablename__ = 'studio_asset'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属项目 id（FK→studio_project）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（冗余免 join，行级隔离）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='素材类型 (script:脚本:blue/image:图片:cyan/audio:音频:purple/video:视频:geekblue/subtitle:字幕:gold/voiceover:配音:magenta/bgm:配乐:lime/font:字体:default)')
    asset_uri: Mapped[str] = mapped_column(sa.String(512), default='', comment='素材本体 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='素材来源 (upload:主人上传:blue/generated:分身生成:green/stock:库存:cyan/provider:外部provider:orange)')
    title: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='素材显示名')
    meta: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='素材元数据 jsonb（时长/分辨率/语言/采样率）')
