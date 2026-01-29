"""代理 API Schema - OpenAI/Anthropic 兼容格式"""

from typing import Literal

from pydantic import Field

from backend.common.schema import SchemaBase

# ==================== OpenAI 兼容格式 ====================


class ChatMessage(SchemaBase):
    """聊天消息"""

    role: Literal['system', 'user', 'assistant', 'tool'] = Field(description='角色')
    content: str | list[dict] | None = Field(default=None, description='内容')
    name: str | None = Field(default=None, description='名称')
    tool_calls: list[dict] | None = Field(default=None, description='工具调用')
    tool_call_id: str | None = Field(default=None, description='工具调用 ID')


class ChatCompletionRequest(SchemaBase):
    """OpenAI Chat Completion 请求"""

    model: str = Field(description='模型名称')
    messages: list[ChatMessage] = Field(description='消息列表')
    temperature: float | None = Field(default=None, ge=0, le=2, description='温度')
    top_p: float | None = Field(default=None, ge=0, le=1, description='Top P')
    n: int | None = Field(default=1, ge=1, le=10, description='生成数量')
    stream: bool = Field(default=False, description='是否流式')
    stop: str | list[str] | None = Field(default=None, description='停止词')
    max_tokens: int | None = Field(default=None, description='最大 tokens')
    presence_penalty: float | None = Field(default=None, ge=-2, le=2, description='存在惩罚')
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2, description='频率惩罚')
    logit_bias: dict[str, float] | None = Field(default=None, description='Logit 偏置')
    user: str | None = Field(default=None, description='用户标识')
    tools: list[dict] | None = Field(default=None, description='工具列表')
    tool_choice: str | dict | None = Field(default=None, description='工具选择')
    response_format: dict | None = Field(default=None, description='响应格式')
    seed: int | None = Field(default=None, description='随机种子')


class ChatCompletionChoice(SchemaBase):
    """Chat Completion 选项"""

    index: int
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionUsage(SchemaBase):
    """Chat Completion 用量"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(SchemaBase):
    """OpenAI Chat Completion 响应"""

    id: str
    object: str = 'chat.completion'
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None
    system_fingerprint: str | None = None


class ChatCompletionChunkDelta(SchemaBase):
    """流式响应增量"""

    role: str | None = None
    content: str | None = None
    tool_calls: list[dict] | None = None


class ChatCompletionChunkChoice(SchemaBase):
    """流式响应选项"""

    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(SchemaBase):
    """流式响应块"""

    id: str
    object: str = 'chat.completion.chunk'
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
    system_fingerprint: str | None = None


# ==================== Anthropic 兼容格式 ====================


class AnthropicContentBlock(SchemaBase):
    """Anthropic 内容块"""

    type: Literal['text', 'image', 'tool_use', 'tool_result'] = Field(description='类型')
    text: str | None = Field(default=None, description='文本内容')
    source: dict | None = Field(default=None, description='图片来源')
    id: str | None = Field(default=None, description='工具调用 ID')
    name: str | None = Field(default=None, description='工具名称')
    input: dict | None = Field(default=None, description='工具输入')
    tool_use_id: str | None = Field(default=None, description='工具使用 ID')
    content: str | None = Field(default=None, description='工具结果内容')


class AnthropicMessage(SchemaBase):
    """Anthropic 消息"""

    role: Literal['user', 'assistant'] = Field(description='角色')
    # content 可以是字符串、AnthropicContentBlock 列表或 dict 列表（包含 tool_result 等）
    content: str | list[AnthropicContentBlock] | list[dict] = Field(description='内容')


class AnthropicMessageRequest(SchemaBase):
    """Anthropic Messages API 请求"""

    model: str = Field(description='模型名称')
    messages: list[AnthropicMessage] = Field(description='消息列表')
    max_tokens: int = Field(description='最大 tokens')
    # system 可以是字符串或数组格式（带 cache_control 等）
    system: str | list[dict] | None = Field(default=None, description='系统提示')
    temperature: float | None = Field(default=None, ge=0, le=1, description='温度')
    top_p: float | None = Field(default=None, ge=0, le=1, description='Top P')
    top_k: int | None = Field(default=None, description='Top K')
    stream: bool = Field(default=False, description='是否流式')
    stop_sequences: list[str] | None = Field(default=None, description='停止序列')
    tools: list[dict] | None = Field(default=None, description='工具列表')
    tool_choice: dict | None = Field(default=None, description='工具选择')
    metadata: dict | None = Field(default=None, description='元数据')


class AnthropicCountTokensRequest(SchemaBase):
    """Anthropic Token 计数请求（不需要 max_tokens）"""

    model: str = Field(description='模型名称')
    messages: list[AnthropicMessage] = Field(description='消息列表')
    system: str | list[dict] | None = Field(default=None, description='系统提示')
    tools: list[dict] | None = Field(default=None, description='工具列表')


class AnthropicUsage(SchemaBase):
    """Anthropic 用量"""

    input_tokens: int
    output_tokens: int


class AnthropicMessageResponse(SchemaBase):
    """Anthropic Messages API 响应"""

    id: str
    type: str = 'message'
    role: str = 'assistant'
    content: list[AnthropicContentBlock]
    model: str
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: AnthropicUsage


# ==================== 通用错误响应 ====================


class ErrorResponse(SchemaBase):
    """错误响应"""

    error: dict = Field(description='错误信息')


class ErrorDetail(SchemaBase):
    """错误详情"""

    message: str
    type: str
    param: str | None = None
    code: str | None = None
