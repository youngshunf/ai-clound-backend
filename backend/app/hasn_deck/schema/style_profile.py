from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StyleProfileSchemaBase(SchemaBase):
    """演示文稿可复用样式 StyleProfile（云端权威，仅 custom）基础模型"""
    slug: str = Field(description='样式 slug（如 minimal-white；同 owner 下唯一；人读标识）')
    label: str = Field(description='展示名')
    description: str | None = Field(None, description='描述（可空）')
    source: str = Field(description='来源 (custom:自定义:blue/override:覆盖内置:orange)')
    design_contract: dict | None = Field(None, description='预设的 DesignContract（JSON）')
    style_prompt: str | None = Field(None, description='注入生成的风格提示词片段')
    owner_id: str = Field(description='归属 owner HASN ID（owner 隔离键）')
    rev: int = Field(description='单调版本（乐观并发 + 同步水位）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删）')


class CreateStyleProfileParam(StyleProfileSchemaBase):
    """创建演示文稿可复用样式 StyleProfile（云端权威，仅 custom）参数"""


class UpdateStyleProfileParam(StyleProfileSchemaBase):
    """更新演示文稿可复用样式 StyleProfile（云端权威，仅 custom）参数"""


class DeleteStyleProfileParam(SchemaBase):
    """删除演示文稿可复用样式 StyleProfile（云端权威，仅 custom）参数"""

    pks: list[int] = Field(description='演示文稿可复用样式 StyleProfile（云端权威，仅 custom） ID 列表')


class GetStyleProfileDetail(StyleProfileSchemaBase):
    """演示文稿可复用样式 StyleProfile（云端权威，仅 custom）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
