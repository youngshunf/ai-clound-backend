import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_community.model._base import CommunityBase
from backend.common.model import id_key


class HasnDocNodes(CommunityBase):
    """文集多级目录树节点表"""

    __tablename__ = 'hasn_doc_nodes'

    id: Mapped[id_key] = mapped_column(init=False)
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='全局唯一 ID，格式 dn_{nanoid}')
    space_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属文集 space_id')
    parent_node_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='父节点 node_id（NULL=文集根下一级）')
    node_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='节点类型 (directory:目录/article:文章叶子)')
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment='目录名 / 文章在树中的显示标题')
    article_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='node_type=article 时指向 hasn_articles.article_id')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='同级排序')
    depth: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='物化深度（根下一级=0）')
    path: Mapped[str] = mapped_column(sa.String(500), default='', comment='物化祖先路径，如 /dn_a/dn_b，便于子树前缀查询')
    visibility: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='可见性 (public:公开/private:私有/password:密码)，NULL=继承最近祖先')
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='visibility=password 时的密码哈希')
    pwd_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='密码版本号，改密自增使旧 grant_token 失效')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/deleted:已删除:red)')
