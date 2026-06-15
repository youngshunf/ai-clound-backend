"""[已搬迁] 加密工具已移至 backend/common/security/encryption.py（D4，2026-06-15）。

此文件保留为向后兼容 shim，仅供 app/llm 内部待删代码继续 import；P6 删除 app/llm 时一并移除。
新代码请直接 `from backend.common.security.encryption import key_encryption, KeyEncryption`。
"""

from backend.common.security.encryption import KeyEncryption, key_encryption

__all__ = ['KeyEncryption', 'key_encryption']
