"""获客项目化 Owner API 的稳定请求契约。"""

from uuid import UUID

from pydantic import Field

from backend.common.schema import SchemaBase


class EnableGrowthProjectBody(SchemaBase):
    """为一个平台项目幂等启用唯一 Growth 漏斗。"""

    platform_project_id: UUID
    name: str | None = Field(None, min_length=1, max_length=200)
    tagline: str | None = Field(None, max_length=500)
