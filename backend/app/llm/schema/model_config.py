"""模型配置 Schema"""

from decimal import Decimal

from pydantic import Field

from backend.app.llm.enums import ModelType
from backend.common.schema import SchemaBase


class ModelConfigBase(SchemaBase):
    """模型配置基础 Schema"""

    provider_id: int = Field(description='供应商 ID')
    model_name: str = Field(description='模型名称')
    display_name: str | None = Field(default=None, description='显示名称')
    model_type: ModelType = Field(description='模型类型')
    max_tokens: int = Field(default=4096, description='最大输出 tokens')
    max_context_length: int = Field(default=8192, description='最大上下文长度')
    supports_streaming: bool = Field(default=True, description='支持流式')
    supports_tools: bool = Field(default=False, description='支持工具调用')
    supports_vision: bool = Field(default=False, description='支持视觉')
    input_cost_per_1k: Decimal = Field(default=Decimal(0), description='输入成本/1K tokens (USD)')
    output_cost_per_1k: Decimal = Field(default=Decimal(0), description='输出成本/1K tokens (USD)')
    rpm_limit: int | None = Field(default=None, description='模型 RPM 限制')
    tpm_limit: int | None = Field(default=None, description='模型 TPM 限制')
    priority: int = Field(default=0, description='优先级(越大越优先)')
    enabled: bool = Field(default=True, description='是否启用')


class CreateModelConfigParam(ModelConfigBase):
    """创建模型配置参数"""


class UpdateModelConfigParam(SchemaBase):
    """更新模型配置参数"""

    provider_id: int | None = Field(default=None, description='供应商 ID')
    model_name: str | None = Field(default=None, description='模型名称')
    display_name: str | None = Field(default=None, description='显示名称')
    model_type: ModelType | None = Field(default=None, description='模型类型')
    max_tokens: int | None = Field(default=None, description='最大输出 tokens')
    max_context_length: int | None = Field(default=None, description='最大上下文长度')
    supports_streaming: bool | None = Field(default=None, description='支持流式')
    supports_tools: bool | None = Field(default=None, description='支持工具调用')
    supports_vision: bool | None = Field(default=None, description='支持视觉')
    input_cost_per_1k: Decimal | None = Field(default=None, description='输入成本/1K tokens (USD)')
    output_cost_per_1k: Decimal | None = Field(default=None, description='输出成本/1K tokens (USD)')
    rpm_limit: int | None = Field(default=None, description='模型 RPM 限制')
    tpm_limit: int | None = Field(default=None, description='模型 TPM 限制')
    priority: int | None = Field(default=None, description='优先级(越大越优先)')
    enabled: bool | None = Field(default=None, description='是否启用')


class GetModelConfigDetail(ModelConfigBase):
    """模型配置详情"""

    id: int
    provider_name: str | None = Field(default=None, description='供应商名称')


class GetModelConfigList(SchemaBase):
    """模型配置列表项"""

    model_config = {'from_attributes': True}

    id: int
    provider_id: int
    provider_name: str | None = None
    model_name: str
    display_name: str | None = None
    model_type: str
    max_tokens: int
    max_context_length: int
    supports_streaming: bool
    supports_tools: bool
    supports_vision: bool
    input_cost_per_1k: Decimal = Decimal(0)
    output_cost_per_1k: Decimal = Decimal(0)
    priority: int
    enabled: bool


class GetAvailableModel(SchemaBase):
    """可用模型（公开接口）- 与 agent-core ModelInfo 对应"""

    model_id: str = Field(description='模型 ID (model_name)')
    provider: str = Field(description='供应商类型')
    display_name: str | None = Field(default=None, description='显示名称')
    max_tokens: int = Field(description='最大输出 tokens')
    model_type: str = Field(description='模型类型 (fast/default/advanced/vision/coding/embedding)')
    supports_streaming: bool = Field(default=True, description='支持流式')
    supports_vision: bool = Field(default=False, description='支持视觉')
    supports_tools: bool = Field(default=True, description='支持工具调用')
    priority: int = Field(default=0, description='优先级(越大越优先)')
    enabled: bool = Field(default=True, description='是否启用')
