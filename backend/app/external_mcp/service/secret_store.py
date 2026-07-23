"""secret:// 凭据存储与生命周期（P7，事实源 10 §7.1 / 02 §6）。

owner 经管理面提交明文 → 服务端 Fernet 加密落库 → 仅返回 `secret://` 引用；
明文**不回显、不写日志、不入同步、不下发设备**。建连时按引用解密注入。

- 写入 `write`：明文 → 加密 → upsert（同 URI 覆盖密文 = 轮换）；
- 解析 `resolve`：`secret://` → 明文（仅网关建连内部用，绝不回 API）；
- 轮换 `rotate`：= 用新明文 `write` 同一 URI；
- 撤销 `revoke`：删除密文 → 该 server 所有 binding 在建连解析阶段失败（软挡提示重配）。

URI 形态（10 §7.1）：`secret://{origin}/{scope}/{key}`，例：
- `secret://system/qcc/bearer-token`（平台 key）
- `secret://owner/{owner_hasn_id}/{server}/{key}`（owner 自带 key）
"""

from __future__ import annotations

import re

from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from backend.app.external_mcp.model import ExternalMcpSecret
from backend.common.security.encryption import key_encryption
from backend.database.db import async_db_session

# secret:// 引用语法：scheme + 至少两段路径（origin/.../key）。
_SECRET_URI_RE = re.compile(r'^secret://[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+$')


def is_secret_ref(value: object) -> bool:
    """是否为合法 `secret://` 引用。"""
    return isinstance(value, str) and value.startswith('secret://') and bool(_SECRET_URI_RE.match(value))


def is_plaintext_credential(value: object) -> bool:
    """config 中是否疑似明文凭据（不含 secret:// 引用的非空字符串 → 拒绝，02 §6）。

    仅对**凭据键**调用（如 Authorization / *_KEY / *_TOKEN）；普通配置值不在此判定。
    判定按「是否含 secret:// 引用」而非「是否以 secret:// 开头」——因为 `Authorization`
    常写成 `Bearer secret://...`（scheme 前缀 + 引用），这类**含引用**的模板不算明文。
    """
    return isinstance(value, str) and bool(value.strip()) and 'secret://' not in value


class SecretStore:
    """external MCP 凭据密文存储（Fernet 加密，明文不落库不回显）。"""

    @staticmethod
    def build_uri(*, origin: str, owner_hasn_id: str | None, server: str, key: str) -> str:
        """构造 secret:// 引用 URI（origin/scope/key）。"""
        scope = owner_hasn_id if (origin != 'system' and owner_hasn_id) else server
        if origin == 'system':
            return f'secret://system/{server}/{key}'
        return f'secret://{origin}/{scope}/{server}/{key}'

    async def write(
        self,
        *,
        secret_uri: str,
        plaintext: str,
        origin: str = 'owner',
        owner_hasn_id: str | None = None,
    ) -> str:
        """写入/轮换：明文加密落库（同 URI upsert）。返回 secret_uri（绝不返回明文）。"""
        if not is_secret_ref(secret_uri):
            raise ValueError(f'非法 secret:// 引用: {secret_uri}')
        if not plaintext or not plaintext.strip():
            raise ValueError('明文凭据不能为空')
        ciphertext = key_encryption.encrypt(plaintext)
        async with async_db_session.begin() as db:
            existing = (
                await db.execute(select(ExternalMcpSecret).where(ExternalMcpSecret.secret_uri == secret_uri))
            ).scalar_one_or_none()
            if existing is not None:
                existing.ciphertext = ciphertext
                existing.origin = origin
                existing.owner_hasn_id = owner_hasn_id
            else:
                db.add(
                    ExternalMcpSecret(
                        secret_uri=secret_uri,
                        origin=origin,
                        owner_hasn_id=owner_hasn_id,
                        ciphertext=ciphertext,
                    )
                )
        return secret_uri

    async def resolve(self, secret_uri: str) -> str | None:
        """解析 secret:// → 明文（仅网关建连内部用）。不存在返回 None（撤销后软挡）。"""
        if not is_secret_ref(secret_uri):
            return None
        async with async_db_session() as db:
            row = (
                await db.execute(
                    select(ExternalMcpSecret.ciphertext).where(ExternalMcpSecret.secret_uri == secret_uri)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return key_encryption.decrypt(row)

    async def revoke(self, secret_uri: str) -> bool:
        """撤销：删除密文。返回是否删除了记录。"""
        async with async_db_session.begin() as db:
            result = cast(
                'CursorResult[Any]',
                await db.execute(delete(ExternalMcpSecret).where(ExternalMcpSecret.secret_uri == secret_uri)),
            )
        return (result.rowcount or 0) > 0

    async def exists(self, secret_uri: str) -> bool:
        """凭据是否已写入（管理面展示是否已配，不回显明文）。"""
        if not is_secret_ref(secret_uri):
            return False
        async with async_db_session() as db:
            row = (
                await db.execute(
                    select(ExternalMcpSecret.id).where(ExternalMcpSecret.secret_uri == secret_uri)
                )
            ).scalar_one_or_none()
        return row is not None


secret_store = SecretStore()
