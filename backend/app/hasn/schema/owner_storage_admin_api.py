"""用户云存储管理端 API 契约。"""

from pydantic import BaseModel, Field


class CreateStorageMigrationParam(BaseModel):
    """为单个 Owner 创建跨存储迁移。"""

    owner_hasn_id: str = Field(min_length=1, max_length=40)
    target_storage_by_access: dict[str, int] = Field(min_length=1, max_length=2)
    observation_seconds: int = Field(default=7 * 24 * 3600, ge=60, le=30 * 24 * 3600)


class RollbackStorageMigrationParam(BaseModel):
    """按批次回滚已切换的迁移明细。"""

    limit: int = Field(default=100, ge=1, le=1000)
