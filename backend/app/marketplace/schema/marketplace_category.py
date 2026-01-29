from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceCategorySchemaBase(SchemaBase):
    """技能市场分类基础模型"""
    slug: str = Field(description='分类标识')
    name: str = Field(description='分类名称')
    icon: str | None = Field(None, description='emoji图标')
    parent_slug: str | None = Field(None, description='父分类标识')
    sort_order: int = Field(description='排序顺序')


class CreateMarketplaceCategoryParam(MarketplaceCategorySchemaBase):
    """创建技能市场分类参数"""


class UpdateMarketplaceCategoryParam(MarketplaceCategorySchemaBase):
    """更新技能市场分类参数"""


class DeleteMarketplaceCategoryParam(SchemaBase):
    """删除技能市场分类参数"""

    pks: list[int] = Field(description='技能市场分类 ID 列表')


class GetMarketplaceCategoryDetail(MarketplaceCategorySchemaBase):
    """技能市场分类详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
