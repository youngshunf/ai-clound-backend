from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageEntriesSchemaBase(SchemaBase):
    """用户云存储逻辑目录项基础模型"""
    entry_id: str = Field(description='目录项稳定 ID')
    owner_hasn_id: str = Field(description='所属主人 hasn_id')
    asset_id: str | None = Field(None, description='文件项关联的逻辑资产 ID')
    parent_entry_id: str | None = Field(None, description='父目录项 ID；根目录为空')
    entry_type: str = Field(description='目录项类型 (file:文件:blue/folder:文件夹:orange)')
    display_name: str = Field(description='用户可见名称')
    normalized_name: str = Field(description='服务端归一化后的冲突判定名称')
    system_category: str | None = Field(None, description='系统目录分类')
    version: int = Field(description='重命名与移动的乐观锁版本')


class CreateHasnStorageEntriesParam(HasnStorageEntriesSchemaBase):
    """创建用户云存储逻辑目录项参数"""


class UpdateHasnStorageEntriesParam(HasnStorageEntriesSchemaBase):
    """更新用户云存储逻辑目录项参数"""


class DeleteHasnStorageEntriesParam(SchemaBase):
    """删除用户云存储逻辑目录项参数"""

    pks: list[int] = Field(description='用户云存储逻辑目录项 ID 列表')


class GetHasnStorageEntriesDetail(HasnStorageEntriesSchemaBase):
    """用户云存储逻辑目录项详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
