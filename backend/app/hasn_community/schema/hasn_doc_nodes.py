from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnDocNodesSchemaBase(SchemaBase):
    """文集多级目录树节点基础模型"""
    node_id: str = Field(description='全局唯一 ID，格式 dn_{nanoid}')
    space_id: str = Field(description='所属文集 space_id')
    parent_node_id: str | None = Field(None, description='父节点 node_id（NULL=文集根下一级）')
    node_type: str = Field(description='节点类型 (directory:目录/article:文章叶子)')
    title: str = Field(description='目录名 / 文章在树中的显示标题')
    article_id: str | None = Field(None, description='node_type=article 时指向 hasn_articles.article_id')
    sort_order: int = Field(description='同级排序')
    depth: int = Field(description='物化深度（根下一级=0）')
    path: str = Field(description='物化祖先路径，如 /dn_a/dn_b，便于子树前缀查询')
    visibility: str | None = Field(None, description='可见性 (public:公开/private:私有/password:密码)，NULL=继承最近祖先')
    password_hash: str | None = Field(None, description='visibility=password 时的密码哈希')
    pwd_version: int = Field(description='密码版本号，改密自增使旧 grant_token 失效')
    status: str = Field(description='状态 (active:正常:green/deleted:已删除:red)')


class CreateHasnDocNodesParam(HasnDocNodesSchemaBase):
    """创建文集多级目录树节点参数"""


class UpdateHasnDocNodesParam(HasnDocNodesSchemaBase):
    """更新文集多级目录树节点参数"""


class DeleteHasnDocNodesParam(SchemaBase):
    """删除文集多级目录树节点参数"""

    pks: list[int] = Field(description='文集多级目录树节点 ID 列表')


class GetHasnDocNodesDetail(HasnDocNodesSchemaBase):
    """文集多级目录树节点详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
