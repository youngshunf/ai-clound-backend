"""模型别名映射 Schema

@author Ysf
"""

from pydantic import BaseModel, Field


class ModelAliasBase(BaseModel):
    """模型别名基础字段"""
    alias_name: str = Field(..., description='别名（Anthropic 模型名称）', max_length=128)
    model_ids: list[int] = Field(default=[], description='映射的模型 ID 列表（按优先级排序）')
    display_name: str | None = Field(None, description='显示名称', max_length=128)
    description: str | None = Field(None, description='描述', max_length=512)
    enabled: bool = Field(True, description='是否启用')


class CreateModelAliasParam(ModelAliasBase):
    """创建模型别名参数"""
    pass


class UpdateModelAliasParam(BaseModel):
    """更新模型别名参数"""
    alias_name: str | None = Field(None, description='别名（Anthropic 模型名称）', max_length=128)
    model_ids: list[int] | None = Field(None, description='映射的模型 ID 列表（按优先级排序）')
    display_name: str | None = Field(None, description='显示名称', max_length=128)
    description: str | None = Field(None, description='描述', max_length=512)
    enabled: bool | None = Field(None, description='是否启用')


class ModelAliasResponse(ModelAliasBase):
    """模型别名响应"""
    id: int = Field(..., description='ID')

    class Config:
        from_attributes = True


class ModelAliasDetailResponse(ModelAliasResponse):
    """模型别名详情响应（包含映射模型信息）"""
    mapped_models: list[dict] = Field(default=[], description='映射的模型详情列表')
