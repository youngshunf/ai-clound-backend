"""B10 — push_tokens.token 应用层静态加密.

使用 Fernet (AES-128-CBC + HMAC-SHA256) 做 PII 静态加密, 密文落 LargeBinary 列
(Postgres BYTEA / SQLite BLOB / MySQL BLOB). 密钥取自 settings.PUSH_TOKEN_ENCRYPTION_KEY,
真实值走 Vault (secret/huanxing/backend/push_token_key); dev 空值允许回退进程内临时
随机密钥 (仅用于本地开发 / 单元测试); 生产 (ENVIRONMENT='prod') 空值应在启动检查中拒绝,
本模块保持容错以便 import 时不崩溃 (验收在 conf 层 / 部署脚本).

规范源: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §13.2.

设计要点 (与 backend/common/security/encryption.py 对齐但独立, 两套密钥分别轮换):
- 单例 Fernet 复用, 避免每次加解密重建
- `EncryptedToken` TypeDecorator 暴露 `str` 给 ORM, 底层存 `bytes`, 避免业务层感知加密
- `reset_fernet_for_tests()` 供单元测试切换临时密钥 / 触发重新初始化
"""
from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from backend.core.conf import settings

_FERNET: Fernet | None = None


def _get_fernet() -> Fernet:
    """Return cached Fernet instance; lazy-init on first call."""
    global _FERNET
    if _FERNET is None:
        key = getattr(settings, 'PUSH_TOKEN_ENCRYPTION_KEY', '') or ''
        if not key:
            # Dev fallback: transient random key; data written in this process
            # cannot be decrypted by a new process (acceptable for tests + local).
            key = Fernet.generate_key().decode()
        _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
    return _FERNET


def reset_fernet_for_tests() -> None:
    """Clear singleton so next call re-reads settings. Test-only."""
    global _FERNET
    _FERNET = None


def encrypt_push_token(plaintext: str) -> bytes:
    """Encrypt UTF-8 plaintext → Fernet ciphertext bytes."""
    return _get_fernet().encrypt(plaintext.encode('utf-8'))


def decrypt_push_token(ciphertext: bytes) -> str:
    """Decrypt Fernet ciphertext bytes → UTF-8 plaintext; raises on tamper."""
    try:
        return _get_fernet().decrypt(ciphertext).decode('utf-8')
    except InvalidToken as exc:
        raise ValueError('push_token ciphertext decryption failed') from exc


class EncryptedToken(TypeDecorator[str]):
    """SQLAlchemy TypeDecorator: ORM-level str ↔ LargeBinary ciphertext.

    Bind: str → Fernet-encrypted bytes (BYTEA/BLOB on disk).
    Result: bytes/memoryview → decrypted str.
    """

    impl = LargeBinary
    cache_ok = True

    @property
    def python_type(self) -> type[str]:
        return str

    def process_bind_param(self, value: str | None, dialect: Any) -> bytes | None:
        if value is None:
            return None
        return encrypt_push_token(value)

    def process_result_value(
        self, value: bytes | memoryview | None, dialect: Any,
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, memoryview):
            value = bytes(value)
        return decrypt_push_token(value)
