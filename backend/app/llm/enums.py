"""LLM 模块枚举定义"""

from enum import StrEnum


class ProviderType(StrEnum):
    """供应商类型（决定 API 接口格式）"""

    OPENAI = 'openai'  # OpenAI 及兼容接口
    ANTHROPIC = 'anthropic'  # Anthropic Claude
    AZURE = 'azure'  # Azure OpenAI
    BEDROCK = 'bedrock'  # AWS Bedrock
    VERTEX_AI = 'vertex_ai'  # Google Vertex AI
    GEMINI = 'gemini'  # Google Gemini
    COHERE = 'cohere'  # Cohere
    MISTRAL = 'mistral'  # Mistral AI
    DEEPSEEK = 'deepseek'  # DeepSeek
    ZHIPU = 'zhipu'  # 智谱 AI
    QWEN = 'qwen'  # 通义千问
    MOONSHOT = 'moonshot'  # Moonshot (Kimi)
    BAICHUAN = 'baichuan'  # 百川
    MINIMAX = 'minimax'  # MiniMax
    OLLAMA = 'ollama'  # Ollama 本地模型


class ModelType(StrEnum):
    """
    模型类型

    与前端 MODEL_TYPES 保持一致
    """

    TEXT = 'TEXT'           # 文本生成
    REASONING = 'REASONING' # 推理
    VISION = 'VISION'       # 视觉
    IMAGE = 'IMAGE'         # 图像生成
    VIDEO = 'VIDEO'         # 视频生成
    EMBEDDING = 'EMBEDDING' # 嵌入
    TTS = 'TTS'             # 语音合成
    STT = 'STT'             # 语音识别


class ApiKeyStatus(StrEnum):
    """API Key 状态"""

    ACTIVE = 'ACTIVE'
    DISABLED = 'DISABLED'
    EXPIRED = 'EXPIRED'
    REVOKED = 'REVOKED'


class UsageLogStatus(StrEnum):
    """用量日志状态"""

    SUCCESS = 'SUCCESS'
    ERROR = 'ERROR'


class CircuitState(StrEnum):
    """熔断器状态"""

    CLOSED = 'CLOSED'
    OPEN = 'OPEN'
    HALF_OPEN = 'HALF_OPEN'
