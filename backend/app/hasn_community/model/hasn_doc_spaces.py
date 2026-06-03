from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class HasnDocSpaces(Base):
    """社区文集/知识库表"""

    __tablename__ = 'hasn_doc_spaces'

    id: Mapped[id_key] = mapped_column(init=False)
    space_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='全局唯一 ID，格式 ds_{nanoid}')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='文集责任主体 hasn_id（Agent 文集 = 主人 hasn_id）')
    author_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='创建者身份 (human:人类/agent:分身)')
    author_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='创建者 hasn_id')
    origin_workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 workspace 类型 (personal:个人/enterprise:企业)')
    origin_workspace_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='来源 workspace 标识')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='文集标题')
    slug: Mapped[str] = mapped_column(sa.String(120), default='', comment='文集在 owner 下唯一，组成公开访问路径')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='文集描述')
    cover_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='封面图 URL')
    default_visibility: Mapped[str] = mapped_column(sa.String(20), default='', comment='文集根缺省可见性 (public:公开:green/private:私有:gray/password:密码:orange)')
    default_password_hash: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='default_visibility=password 时的密码哈希')
    node_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='节点数（冗余，异步维护）')
    article_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='文章数（冗余，异步维护）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/archived:已归档:gray/deleted:已删除:red)')
