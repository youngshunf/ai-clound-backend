"""旧 RAGFlow 密文（bytea）编解码工具。

实施 14-AI-Native应用平台/实施/03 P5 收编后，知识库凭据已统一用
``key_encryption`` 加密、明文 str 存 ``hasn_app_credential.credential_ref``，
不再产生新的 bytea 密文。本模块**仅保留**给一次性数据迁移
``scripts/migrate_knowledge_to_app_instance.py`` 解密「删表前」的存量旧密文用
（底层即 ``key_encryption``，但需处理 bytes/legacy 明文兜底）。新代码请勿调用。
"""

from __future__ import annotations

from backend.app.llm.core.encryption import key_encryption


def encrypt_ragflow_secret(plaintext: str) -> bytes:
    if plaintext == "":
        return b""
    return key_encryption.encrypt(plaintext).encode("utf-8")


def decrypt_ragflow_secret(ciphertext: bytes | str | None) -> str:
    if ciphertext in (None, b"", ""):
        return ""
    encoded = ciphertext.decode("utf-8") if isinstance(ciphertext, bytes) else ciphertext
    try:
        return key_encryption.decrypt(encoded)
    except Exception:
        return encoded
