from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CreateFolderParam(SchemaBase):
    """创建目录参数"""
    name: str = Field(description='目录名称')
    parent_id: int | None = Field(None, description='父目录ID（空=根目录）')
    icon: str | None = Field(None, description='图标（emoji或icon名）')
    description: str | None = Field(None, description='目录描述')


class UpdateFolderParam(SchemaBase):
    """更新目录参数"""
    name: str | None = Field(None, description='目录名称')
    icon: str | None = Field(None, description='图标')
    description: str | None = Field(None, description='目录描述')
    sort_order: int | None = Field(None, description='排序权重')


class MoveFolderParam(SchemaBase):
    """移动目录参数"""
    target_parent_id: int | None = Field(None, description='目标父目录ID（NULL=移到根目录）')


class MoveDocumentParam(SchemaBase):
    """移动文档到目录参数"""
    target_folder_id: int | None = Field(None, description='目标目录ID（NULL=移到根目录）')


class DeleteFolderParam(SchemaBase):
    """删除目录参数"""
    pks: list[int] = Field(description='目录 ID 列表')


class FolderTreeNode(SchemaBase):
    """目录树节点（递归结构）"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    uuid: str
    name: str
    icon: str | None = None
    parent_id: int | None = None
    sort_order: int = 0
    doc_count: int = 0
    children: list['FolderTreeNode'] = Field(default_factory=list)


class GetFolderDetail(SchemaBase):
    """目录详情"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    uuid: str
    user_id: int
    name: str
    parent_id: int | None = None
    path: str = '/'
    sort_order: int = 0
    icon: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    created_time: datetime
    updated_time: datetime | None = None
