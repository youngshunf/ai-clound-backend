from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceSkillVersionSchemaBase(SchemaBase):
    """技能版本基础模型"""
    skill_id: str = Field(description='关联的技能ID')
    version: str = Field(description='语义化版本号')
    changelog: str | None = Field(None, description='版本更新日志')
    package_url: str | None = Field(None, description='完整包下载URL')
    file_hash: str | None = Field(None, description='SHA256校验值')
    file_size: int | None = Field(None, description='包大小（字节）')
    is_latest: bool = Field(description='是否为最新版本')
    published_at: datetime = Field(description='发布时间')


class CreateMarketplaceSkillVersionParam(MarketplaceSkillVersionSchemaBase):
    """创建技能版本参数"""


class UpdateMarketplaceSkillVersionParam(MarketplaceSkillVersionSchemaBase):
    """更新技能版本参数"""


class DeleteMarketplaceSkillVersionParam(SchemaBase):
    """删除技能版本参数"""

    pks: list[int] = Field(description='技能版本 ID 列表')


class GetMarketplaceSkillVersionDetail(MarketplaceSkillVersionSchemaBase):
    """技能版本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
