"""第三方 MCP 网关 P7-G（local_process / 本地进程型）云端真实集成测试（事实源 doc101 §2.1）。

零 mock 原则：真实本地 PostgreSQL(15432)，跑 local_process 注册 + secret:// 写入 + resolve-env
解析全链路，结束清理自建行。

覆盖（doc101 §0.1 / §2.1.2）：
1. 注册校验 —— local_process/stdio 带 command/args/env 落库；缺 command / system-origin / 明文 env 被拒。
2. resolve-env —— env 的 secret:// 引用建连时解析为明文（仅 owner 自己 daemon）；撤销后 CREDENTIAL_MISSING。
3. 安全闸 —— remote_service 拒解析；跨 owner 拒解析（行级隔离）。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from sqlalchemy import delete

from backend.app.external_mcp.model import ExternalMcpSecret, ExternalMcpServer
from backend.app.external_mcp.service.gateway_service import external_mcp_gateway
from backend.app.external_mcp.service.secret_store import secret_store
from backend.app.external_mcp.service.validation import RegistrationError
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.database.db import async_db_session

# 真实-DB async 测试共享 module 级事件循环（连接池不被已关闭 loop 回收）。
pytestmark = pytest.mark.asyncio(loop_scope='module')


def _suffix() -> str:
    return uuid4().hex[:10]


async def _cleanup(mcp_id: str, *, secret_uri: str | None = None) -> None:
    async with async_db_session.begin() as db:
        await db.execute(delete(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
        if secret_uri:
            await db.execute(delete(ExternalMcpSecret).where(ExternalMcpSecret.secret_uri == secret_uri))


async def test_register_local_process_stdio_persists() -> None:
    """local_process/stdio 注册：command/args/env(secret:// 引用) 落库，hosting=local_process。"""
    owner = f'o_{_suffix()}'
    name = f'gmail_{_suffix()}'
    secret_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='token')
    server = await external_mcp_gateway.register_server(
        name=name,
        hosting='local_process',
        transport='stdio',
        origin='owner',
        owner_hasn_id=owner,
        command='npx',
        args=['-y', '@modelcontextprotocol/server-gmail'],
        env={'GMAIL_TOKEN': secret_uri},
    )
    mcp_id = server['mcp_id']
    try:
        assert server['hosting'] == 'local_process'
        assert server['transport'] == 'stdio'
        assert server['command'] == 'npx'
        assert server['args'] == ['-y', '@modelcontextprotocol/server-gmail']
        assert server['env'] == {'GMAIL_TOKEN': secret_uri}
    finally:
        await _cleanup(mcp_id)


async def test_register_local_process_stdio_requires_command() -> None:
    """local_process/stdio 缺 command → 注册期 RegistrationError（02 §4.1）。"""
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'nocmd_{_suffix()}',
            hosting='local_process',
            transport='stdio',
            origin='owner',
            owner_hasn_id=f'o_{_suffix()}',
            command=None,
        )


async def test_register_local_process_rejects_system_origin() -> None:
    """local_process + system-origin → 拒（平台 key 绝不下发设备，doc101 §0.1）。"""
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'sys_{_suffix()}',
            hosting='local_process',
            transport='stdio',
            origin='system',
            owner_hasn_id=None,
            command='npx',
        )


async def test_register_local_process_rejects_plaintext_env_credential() -> None:
    """env 凭据键含明文（非 secret:// 引用）→ 注册期拒（02 §6 加载即校验）。"""
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'plain_{_suffix()}',
            hosting='local_process',
            transport='stdio',
            origin='owner',
            owner_hasn_id=f'o_{_suffix()}',
            command='npx',
            env={'API_TOKEN': 'sk-plaintext-leaked'},
        )


async def test_resolve_env_for_owner_resolves_secret_refs() -> None:
    """resolve-env：env 的 secret:// 引用解析为明文（仅下发 owner 自己 daemon）；非凭据键原样透传。"""
    owner = f'o_{_suffix()}'
    name = f'local_{_suffix()}'
    secret_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='token')
    await secret_store.write(secret_uri=secret_uri, plaintext='tok-XYZ-789', origin='owner', owner_hasn_id=owner)
    server = await external_mcp_gateway.register_server(
        name=name,
        hosting='local_process',
        transport='stdio',
        origin='owner',
        owner_hasn_id=owner,
        command='./bin/server',
        env={'API_TOKEN': secret_uri, 'LOG_LEVEL': 'info'},
    )
    mcp_id = server['mcp_id']
    try:
        resolved = await external_mcp_gateway.resolve_env_for_owner(mcp_id=mcp_id, owner_hasn_id=owner)
        # secret:// 引用 → 明文；普通值原样。
        assert resolved == {'API_TOKEN': 'tok-XYZ-789', 'LOG_LEVEL': 'info'}
    finally:
        await _cleanup(mcp_id, secret_uri=secret_uri)


async def test_resolve_env_credential_missing_after_revoke() -> None:
    """撤销凭据后 resolve-env → CREDENTIAL_MISSING（撤销后软挡，doc101 §2.1.2）。"""
    owner = f'o_{_suffix()}'
    name = f'revoked_{_suffix()}'
    secret_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='token')
    await secret_store.write(secret_uri=secret_uri, plaintext='tok-to-revoke', origin='owner', owner_hasn_id=owner)
    server = await external_mcp_gateway.register_server(
        name=name,
        hosting='local_process',
        transport='stdio',
        origin='owner',
        owner_hasn_id=owner,
        command='./bin/server',
        env={'API_TOKEN': secret_uri},
    )
    mcp_id = server['mcp_id']
    try:
        # 先撤销密文。
        await secret_store.revoke(secret_uri)
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.resolve_env_for_owner(mcp_id=mcp_id, owner_hasn_id=owner)
        assert exc.value.code == McpErrorCode.CREDENTIAL_MISSING
    finally:
        await _cleanup(mcp_id, secret_uri=secret_uri)


async def test_resolve_env_rejects_remote_service() -> None:
    """remote_service server 调 resolve-env → 拒（凭据云端建连，绝不下发设备）。"""
    owner = f'o_{_suffix()}'
    server = await external_mcp_gateway.register_server(
        name=f'remote_{_suffix()}',
        hosting='remote_service',
        transport='http',
        origin='owner',
        owner_hasn_id=owner,
        endpoint='https://mcp.example.com/stream',
    )
    mcp_id = server['mcp_id']
    try:
        with pytest.raises(RegistrationError):
            await external_mcp_gateway.resolve_env_for_owner(mcp_id=mcp_id, owner_hasn_id=owner)
    finally:
        await _cleanup(mcp_id)


async def test_resolve_env_rejects_cross_owner() -> None:
    """别的 owner 调 resolve-env → DIRECT_CALL_DENIED（行级隔离）。"""
    owner = f'o_{_suffix()}'
    other = f'o_{_suffix()}'
    name = f'mine_{_suffix()}'
    secret_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='token')
    await secret_store.write(secret_uri=secret_uri, plaintext='mine', origin='owner', owner_hasn_id=owner)
    server = await external_mcp_gateway.register_server(
        name=name,
        hosting='local_process',
        transport='stdio',
        origin='owner',
        owner_hasn_id=owner,
        command='./bin/server',
        env={'API_TOKEN': secret_uri},
    )
    mcp_id = server['mcp_id']
    try:
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.resolve_env_for_owner(mcp_id=mcp_id, owner_hasn_id=other)
        assert exc.value.code == McpErrorCode.DIRECT_CALL_DENIED
    finally:
        await _cleanup(mcp_id, secret_uri=secret_uri)
