from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnDocSpacesSchemaBase(SchemaBase):
    """社区文集/知识库基础模型"""
    space_id: str = Field(description='全局唯一 ID，格式 ds_{nanoid}')
    owner_hasn_id: str = Field(description='文集责任主体 hasn_id（Agent 文集 = 主人 hasn_id）')
    author_type: str = Field(description='创建者身份 (human:人类/agent:分身)')
    author_hasn_id: str = Field(description='创建者 hasn_id')
    origin_workspace_kind: str = Field(description='来源 workspace 类型 (personal:个人/enterprise:企业)')
    origin_workspace_id: str = Field(description='来源 workspace 标识')
    title: str = Field(description='文集标题')
    slug: str = Field(description='文集在 owner 下唯一，组成公开访问路径')
    description: str | None = Field(None, description='文集描述')
    cover_url: str | None = Field(None, description='封面图 URL')
    default_visibility: str = Field(description='文集根缺省可见性 (public:公开:green/private:私有:gray/password:密码:orange)')
    default_password_hash: str | None = Field(None, description='default_visibility=password 时的密码哈希')
    node_count: int = Field(description='节点数（冗余，异步维护）')
    article_count: int = Field(description='文章数（冗余，异步维护）')
    status: str = Field(description='状态 (active:正常:green/archived:已归档:gray/deleted:已删除:red)')


class CreateHasnDocSpacesParam(HasnDocSpacesSchemaBase):
    """创建社区文集/知识库参数"""


class UpdateHasnDocSpacesParam(HasnDocSpacesSchemaBase):
    """更新社区文集/知识库参数"""


class DeleteHasnDocSpacesParam(SchemaBase):
    """删除社区文集/知识库参数"""

    pks: list[int] = Field(description='社区文集/知识库 ID 列表')


class GetHasnDocSpacesDetail(HasnDocSpacesSchemaBase):
    """社区文集/知识库详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
