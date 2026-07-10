import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import UniversalText, id_key


class Platform(HasnCreatorAppBase):
    """平台目录（选择制，含主页根 URL/主页模板/指标口径）；账号/竞品/项目的 platform 一律选自此表"""

    __tablename__ = 'platform'

    id: Mapped[id_key] = mapped_column(init=False)
    key: Mapped[str] = mapped_column(sa.String(40), default='', comment='平台英文 key（xiaohongshu/douyin/...）')
    name: Mapped[str] = mapped_column(sa.String(50), default='', comment='平台中文名（小红书/抖音/...）')
    color: Mapped[str] = mapped_column(sa.String(20), default='gray', comment='品牌色（red/gray/... 或 hex）')
    home_url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='平台主页根 URL')
    profile_tpl: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='主页链接模板（{uid} 占位）')
    metrics_labels: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='该平台指标叫法 {followers,likes,posts,favorites}'
    )
    has_public_home: Mapped[bool] = mapped_column(
        sa.Boolean(), default=True, comment='是否有公开网页主页（公众号/视频号=false → home_url 必填豁免）'
    )
    supports_publish: Mapped[bool] = mapped_column(
        sa.Boolean(), default=False, comment='是否支持 api_auto 发布'
    )
    sort: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序')
    is_builtin: Mapped[bool] = mapped_column(sa.Boolean(), default=True, comment='内置 seed（true 不可删）')
