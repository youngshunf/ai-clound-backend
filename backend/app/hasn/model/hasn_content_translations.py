from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnContentTranslations(Base):
    """用户内容译文缓存（译文是视图，不回写原文表）"""

    __tablename__ = 'hasn_content_translations'

    id: Mapped[id_key] = mapped_column(init=False)
    resource_kind: Mapped[str] = mapped_column(sa.String(32), default='', comment='资源类型 (post:帖子/article:文章/comment:评论/circle:圈子/profile:名片)')
    resource_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='资源的云端权威 ID（post_id / article_id / comment_id ...）')
    field: Mapped[str] = mapped_column(sa.String(32), default='', comment='被翻字段 (content:正文/title:标题/summary:摘要)')
    source_lang: Mapped[str] = mapped_column(sa.String(16), default='', comment='原文语言（检测所得，如 zh / en）')
    target_lang: Mapped[str] = mapped_column(sa.String(16), default='', comment='目标语言（如 en / ja / zh-TW）')
    source_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='原文 sha256；原文改动即自动失效并重译')
    translated_text: Mapped[str] = mapped_column(UniversalText, default='', comment='译文正文')
    engine: Mapped[str] = mapped_column(sa.String(64), default='', comment='翻译引擎/模型名，如 agnes-2.5-flash')
    engine_version: Mapped[str] = mapped_column(sa.String(32), default='', comment='翻译管线版本；升级 prompt/模型时递增以整体失效')
    token_usage: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='本次翻译消耗 token 数，便于事后成本核算')
    hit_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='缓存命中次数（共享缓存摊薄效果的观测指标）')
