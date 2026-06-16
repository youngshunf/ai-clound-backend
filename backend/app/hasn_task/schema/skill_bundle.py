from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSkillBundleSchemaBase(SchemaBase):
    """Skill Bundle 定义表（多个 skill 的组合）基础模型"""

    owner_id: str = Field(description='Bundle 归属 owner')
    name: str = Field(description='Bundle 名称（唯一标识）')
    display_name: str | None = Field(None, description='显示名称')
    description: str | None = Field(None, description='描述')
    skill_ids: list[str] = Field(
        default_factory=list, description='Skill 名称列表，如 ["github-code-review", "test-driven-development"]'
    )
    instruction: str | None = Field(None, description='可选的额外指导语，会在加载 skills 前注入')
    created_time: datetime | None = Field(None, description='创建时间')
    updated_time: datetime | None = Field(None, description='更新时间')


class CreateHasnSkillBundleParam(HasnSkillBundleSchemaBase):
    """创建Skill Bundle 定义表（多个 skill 的组合）参数"""


class UpdateHasnSkillBundleParam(HasnSkillBundleSchemaBase):
    """更新Skill Bundle 定义表（多个 skill 的组合）参数"""


class DeleteHasnSkillBundleParam(SchemaBase):
    """删除Skill Bundle 定义表（多个 skill 的组合）参数"""

    pks: list[int] = Field(description='Skill Bundle 定义表（多个 skill 的组合） ID 列表')


class GetHasnSkillBundleDetail(HasnSkillBundleSchemaBase):
    """Skill Bundle 定义表（多个 skill 的组合）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
