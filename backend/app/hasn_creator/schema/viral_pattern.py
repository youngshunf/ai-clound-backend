from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ViralPatternSchemaBase(SchemaBase):
    """爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）基础模型"""
    project_id: int | None = Field(None, description='归属项目（可空=全局通用）')
    user_id: int | None = Field(None, description='None')
    enterprise_id: int | None = Field(None, description='None')
    owner_scope: str = Field(description='None')
    name: str = Field(description='None')
    pattern_type: str = Field(description='类型 (hook:钩子:blue/structure:结构:purple/title:标题:orange/cta:行动号召:green)')
    template: str | None = Field(None, description='模板（如「3 步搞定 X」标题模板）')
    description: str | None = Field(None, description='None')
    example: str | None = Field(None, description='None')
    usage_count: int = Field(description='None')
    success_rate: Decimal = Field(description='None')
    source: str = Field(description='来源 (ai_extracted:AI提炼:violet/manual:手动:blue/builtin:内置:gray)')
    tags: dict = Field(description='None')
    is_builtin: bool = Field(description='None')


class CreateViralPatternParam(ViralPatternSchemaBase):
    """创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数"""


class UpdateViralPatternParam(ViralPatternSchemaBase):
    """更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数"""


class DeleteViralPatternParam(SchemaBase):
    """删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数"""

    pks: list[int] = Field(description='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID 列表')


class GetViralPatternDetail(ViralPatternSchemaBase):
    """爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
