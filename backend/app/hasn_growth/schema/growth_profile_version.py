from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProfileVersionSchemaBase(SchemaBase):
    """获客项目已确认画像的不可变版本历史基础模型"""

    growth_project_id: str | UUID = Field(description='None')
    version: int = Field(description='None')
    product_profile: dict = Field(description='None')
    icp_profile: dict = Field(description='None')
    knowledge_document_versions: list[dict] = Field(
        description='参与画像确认的 Knowledge 文档及版本 [{document_id,version}]'
    )
    source_hash: str = Field(description='参与文档稳定 ID 与版本的规范化 SHA256')
    confirmed_by_kind: str = Field(description='确认主体 (owner:主人:blue/migration:迁移:gray)')
    confirmed_by_id: str = Field(description='None')


class CreateGrowthProfileVersionParam(GrowthProfileVersionSchemaBase):
    """创建获客项目已确认画像的不可变版本历史参数"""


class UpdateGrowthProfileVersionParam(GrowthProfileVersionSchemaBase):
    """更新获客项目已确认画像的不可变版本历史参数"""


class DeleteGrowthProfileVersionParam(SchemaBase):
    """删除获客项目已确认画像的不可变版本历史参数"""

    pks: list[int] = Field(description='获客项目已确认画像的不可变版本历史 ID 列表')


class GetGrowthProfileVersionDetail(GrowthProfileVersionSchemaBase):
    """获客项目已确认画像的不可变版本历史详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
