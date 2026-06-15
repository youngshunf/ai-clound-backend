"""new-api 自建 API Key 体系枚举（从 app/llm/enums.py 摘出 ApiKeyStatus，D1）。"""

from enum import StrEnum


class ApiKeyStatus(StrEnum):
    """API Key 状态"""

    ACTIVE = 'ACTIVE'
    DISABLED = 'DISABLED'
    EXPIRED = 'EXPIRED'
    REVOKED = 'REVOKED'
