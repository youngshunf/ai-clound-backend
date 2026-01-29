from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceSkillSchemaBase(SchemaBase):
    """技能市场技能基础模型"""
    skill_id: str = Field(description='技能唯一标识')
    name: str = Field(description='技能名称')
    description: str | None = Field(None, description='技能描述')
    icon_url: str | None = Field(None, description='SVG图标URL')
    author_id: int | None = Field(None, description='作者用户ID')
    author_name: str | None = Field(None, description='作者名称')
    category: str | None = Field(None, description='分类')
    tags: str | None = Field(None, description='标签，逗号分隔')
    pricing_type: str = Field(description='定价类型 (free:免费:green/paid:付费:orange)')
    price: Decimal = Field(description='价格')
    is_private: bool = Field(description='是否私有')
    is_official: bool = Field(description='是否官方技能')
    download_count: int = Field(description='下载次数')


class CreateMarketplaceSkillParam(MarketplaceSkillSchemaBase):
    """创建技能市场技能参数"""


class UpdateMarketplaceSkillParam(MarketplaceSkillSchemaBase):
    """更新技能市场技能参数"""


class DeleteMarketplaceSkillParam(SchemaBase):
    """删除技能市场技能参数"""

    pks: list[int] = Field(description='技能市场技能 ID 列表')


class GetMarketplaceSkillDetail(MarketplaceSkillSchemaBase):
    """技能市场技能详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
