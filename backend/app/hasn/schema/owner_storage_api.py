"""用户私有存储 Owner API 契约。"""

from pydantic import BaseModel, Field


class CreateStorageFolderParam(BaseModel):
    """新建逻辑文件夹。"""

    name: str = Field(min_length=1, max_length=1024)
    parent_entry_id: str | None = Field(default=None, max_length=40)


class UpdateStorageEntryParam(BaseModel):
    """以乐观锁重命名或移动目录项。"""

    version: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=1024)
    parent_entry_id: str | None = Field(default=None, max_length=40)


class SaveStorageAssetParam(BaseModel):
    """把可读源资产保存为当前 Owner 的独立副本。"""

    idempotency_key: str = Field(min_length=1, max_length=128)
    parent_entry_id: str | None = Field(default=None, max_length=40)
    display_name: str | None = Field(default=None, min_length=1, max_length=1024)


class CreateStorageExportParam(BaseModel):
    """创建存储导出作业。"""

    mode: str = Field(default='manifest', pattern='^(manifest|archive)$')
    include_trashed: bool = False
