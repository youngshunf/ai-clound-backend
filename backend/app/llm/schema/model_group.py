"""模型组 Schema"""

from pydantic import Field, computed_field

from backend.app.llm.enums import ModelType
from backend.common.schema import SchemaBase


class ModelGroupBase(SchemaBase):
    """模型组基础 Schema"""

    name: str = Field(description='组名称')
    model_type: ModelType = Field(description='模型类型')
    model_ids: list[int] = Field(default_factory=list, description='模型 ID 列表(按优先级排序)')
    fallback_enabled: bool = Field(default=True, description='启用故障转移')
    retry_count: int = Field(default=3, description='重试次数')
    timeout_seconds: int = Field(default=60, description='超时秒数')
    enabled: bool = Field(default=True, description='是否启用')
    description: str | None = Field(default=None, description='描述')


class CreateModelGroupParam(ModelGroupBase):
    """创建模型组参数"""

    pass


class UpdateModelGroupParam(SchemaBase):
    """更新模型组参数"""

    name: str | None = Field(default=None, description='组名称')
    model_type: ModelType | None = Field(default=None, description='模型类型')
    model_ids: list[int] | None = Field(default=None, description='模型 ID 列表')
    fallback_enabled: bool | None = Field(default=None, description='启用故障转移')
    retry_count: int | None = Field(default=None, description='重试次数')
    timeout_seconds: int | None = Field(default=None, description='超时秒数')
    enabled: bool | None = Field(default=None, description='是否启用')
    description: str | None = Field(default=None, description='描述')


class GetModelGroupDetail(ModelGroupBase):
    """模型组详情"""

    model_config = {'from_attributes': True}

    id: int


class GetModelGroupList(SchemaBase):
    """模型组列表项"""

    model_config = {'from_attributes': True}

    id: int
    name: str
    model_type: str
    model_ids: list[int] = Field(default_factory=list, exclude=True)
    fallback_enabled: bool
    enabled: bool
    description: str | None = None

    @computed_field
    @property
    def model_count(self) -> int:
        """模型数量"""
        return len(self.model_ids) if self.model_ids else 0
