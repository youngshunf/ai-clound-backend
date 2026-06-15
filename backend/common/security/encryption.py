"""通用密钥加密工具（Fernet 对称加密 + SHA-256 哈希）。

从 `app/llm/core/encryption.py` 搬迁至此（D4，2026-06-15 new-api 解耦迁移）：它是全站共享的
凭据加密原语（hasn 工作台/应用实例/MCP key、hasn_knowledge RAGFlow service key、new-api API
Key 体系等都依赖），与 LLM 网关无关，不应留在被删的 `app/llm`。

⚠️ 密钥配置键名 `settings.LLM_ENCRYPTION_KEY` **保持不变**——改名会导致历史密文解不开。
"""

import hashlib
import secrets

from cryptography.fernet import Fernet

from backend.core.conf import settings


class KeyEncryption:
    """密钥加密工具（Fernet 对称加密 + SHA-256 哈希）。"""

    def __init__(self, encryption_key: str | None = None) -> None:
        """
        初始化加密工具

        :param encryption_key: Fernet 加密密钥 (base64 编码的 32 字节密钥)
        """
        self._key = encryption_key or getattr(settings, 'LLM_ENCRYPTION_KEY', None)
        if self._key:
            self._fernet = Fernet(self._key.encode() if isinstance(self._key, str) else self._key)
        else:
            # 如果没有配置密钥，生成一个临时密钥（仅用于开发）
            self._fernet = Fernet(Fernet.generate_key())

    def encrypt(self, plaintext: str) -> str:
        """
        对称加密

        :param plaintext: 明文
        :return: 加密后的密文 (base64 编码)
        """
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        对称解密

        :param ciphertext: 密文 (base64 编码)
        :return: 解密后的明文
        """
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def hash_key(api_key: str) -> str:
        """
        SHA-256 哈希

        :param api_key: API Key
        :return: 哈希值 (64 字符十六进制)
        """
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def generate_api_key(prefix: str = 'sk-hx') -> tuple[str, str]:
        """
        生成 API Key

        :param prefix: Key 前缀
        :return: (完整 key, 前缀显示)
        """
        random_part = secrets.token_urlsafe(32)
        full_key = f'{prefix}-{random_part}'
        display_prefix = f'{prefix}-{random_part[:4]}...'
        return full_key, display_prefix

    @staticmethod
    def generate_encryption_key() -> str:
        """
        生成新的 Fernet 加密密钥

        :return: base64 编码的密钥
        """
        return Fernet.generate_key().decode()


# 全局加密工具单例
key_encryption = KeyEncryption()
