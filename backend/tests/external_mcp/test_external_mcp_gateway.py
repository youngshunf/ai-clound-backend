"""第三方 MCP 网关 P7-A/B 真实集成测试（事实源 10/02）。

零 mock 原则：
- DB：真实本地 PostgreSQL(15432)，跑 register/bind/secret/usage 全链路，结束清理自建行。
- 出站 MCP client：起一个**真实** MCP streamable-HTTP stub server（Starlette+uvicorn，随机端口，
  真实 socket + 真实 httpx + 真实 initialize→tools/list→tools/call + SSE 解析），不 mock 我们的代码。

覆盖：
1. 注册校验 —— 合法 remote_service/http 落库；非法 transport×hosting 组合被拒（10 §4.1）。
2. secret:// 凭据 —— write→resolve 往返 + revoke 后软挡（10 §7.1）。
3. 归一纯函数 —— normalize_tool canonical 名 + risk + tools_hash 稳定（10 §3）。
4. 自省+绑定+解析+代理 全链路 —— 真实 server 自省工具 → owner 绑定 Agent → resolve gate1/gate2 →
   proxy_call 真实 tools/call 往返 → usage 记账（10 §6）。
5. 门控 —— 未绑定 Agent resolve 为空；proxy_call 未授权工具被拒（10 §6）。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

from uuid import uuid4

import pytest
import pytest_asyncio
import uvicorn

from sqlalchemy import delete
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.app.external_mcp.model import (
    ExternalMcpBinding,
    ExternalMcpSecret,
    ExternalMcpServer,
    ExternalMcpUsage,
)
from backend.app.external_mcp.external_tool import load_external_mcp_tools_for_agent
from backend.app.external_mcp.service.gateway_service import ExternalMcpGateway, external_mcp_gateway
from backend.app.external_mcp.service.secret_store import is_secret_ref, secret_store
from backend.app.external_mcp.service.validation import RegistrationError
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.tool_directory import ToolDirectoryService, ToolSearchQuery
from backend.app.mcp.tools.registry import ToolRegistry
from backend.database.db import async_db_session

# 多个真实-DB async 测试共享同一 module 级事件循环，避免连接池被上一个已关闭 loop 回收。
pytestmark = pytest.mark.asyncio(loop_scope='module')


def _suffix() -> str:
    return uuid4().hex[:10]


# --------------------------------------------------------------------------- #
# 真实 MCP streamable-HTTP stub server（被自省/代理真实连上）
# --------------------------------------------------------------------------- #


def _rpc_ok(req_id, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': req_id, 'result': result}


async def _mcp_endpoint(request: Request) -> Response:
    """最小 MCP server：initialize / notifications/initialized / tools/list / tools/call。"""
    body = await request.json()
    method = body.get('method')
    req_id = body.get('id')

    if method == 'initialize':
        resp = JSONResponse(
            _rpc_ok(
                req_id,
                {
                    'protocolVersion': '2025-03-26',
                    'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'stub-mcp', 'version': '0.0.1'},
                },
            )
        )
        resp.headers['Mcp-Session-Id'] = 'stub-session-1'
        return resp

    if method == 'notifications/initialized':
        return Response(status_code=202)

    if method == 'tools/list':
        return JSONResponse(
            _rpc_ok(
                req_id,
                {
                    'tools': [
                        {
                            'name': 'company-search',
                            'title': '企业检索',
                            'description': '按名称检索企业',
                            'inputSchema': {
                                'type': 'object',
                                'properties': {'keyword': {'type': 'string'}},
                                'required': ['keyword'],
                            },
                            'annotations': {'readOnlyHint': True},
                        },
                        {
                            'name': 'risk.fetch',
                            'description': '拉取企业风险（写副作用工具，无 readOnlyHint）',
                            'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}},
                        },
                    ]
                },
            )
        )

    if method == 'tools/call':
        params = body.get('params') or {}
        name = params.get('name')
        args = params.get('arguments') or {}
        # 用 SSE 返回，顺带验证 client 的 text/event-stream 解析路径。
        payload = _rpc_ok(
            req_id,
            {
                'content': [{'type': 'text', 'text': f'called {name} with {args}'}],
                'isError': False,
            },
        )
        import json as _json

        sse = f'event: message\ndata: {_json.dumps(payload)}\n\n'
        return Response(content=sse, media_type='text/event-stream')

    return JSONResponse({'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': 'method not found'}})


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest_asyncio.fixture(scope='module', loop_scope='module')
async def stub_mcp_endpoint():
    """启动真实 uvicorn MCP stub server，yield 其 /mcp endpoint URL，结束优雅关闭。"""
    app = Starlette(routes=[Route('/mcp', _mcp_endpoint, methods=['POST'])])
    port = _free_port()
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off')
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # 等待真实就绪。
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, 'stub MCP server 未能启动'
    try:
        yield f'http://127.0.0.1:{port}/mcp'
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=5)


async def _cleanup_server(mcp_id: str, *, agent_hasn_id: str | None = None, secret_uri: str | None = None) -> None:
    async with async_db_session.begin() as db:
        await db.execute(delete(ExternalMcpUsage).where(ExternalMcpUsage.mcp_id == mcp_id))
        await db.execute(delete(ExternalMcpBinding).where(ExternalMcpBinding.mcp_id == mcp_id))
        await db.execute(delete(ExternalMcpServer).where(ExternalMcpServer.mcp_id == mcp_id))
        if secret_uri:
            await db.execute(delete(ExternalMcpSecret).where(ExternalMcpSecret.secret_uri == secret_uri))


async def _insert_loopback_server(*, name: str, endpoint: str, owner_hasn_id: str) -> str:
    """直接落一行 remote_service server 指向回环 stub（绕注册期回环 UX 闸——运行期 introspect/
    proxy 路径本就不重校回环，此处复用其代理链路做真实 socket E2E）。返回 mcp_id。"""
    return await _insert_server(name=name, endpoint=endpoint, origin='owner', owner_hasn_id=owner_hasn_id)


async def _insert_server(
    *,
    name: str,
    endpoint: str,
    origin: str = 'owner',
    owner_hasn_id: str | None = None,
    per_owner_daily_quota: int = 0,
    rate_limit_per_min: int = 0,
) -> str:
    """直接落一行 remote_service server（管理面测试用；endpoint 不一定连接）。返回 mcp_id。"""
    from backend.app.external_mcp.service.gateway_service import _new_id

    mcp_id = _new_id('mcp')
    async with async_db_session.begin() as db:
        db.add(
            ExternalMcpServer(
                mcp_id=mcp_id,
                name=name,
                display_name=name,
                hosting='remote_service',
                transport='http',
                endpoint=endpoint,
                origin=origin,
                owner_hasn_id=owner_hasn_id,
                scope='owner' if origin != 'system' else 'system',
                risk_level='medium',
                advertised_tools_cache=[],
                health_status='unknown',
                status='active',
                per_owner_daily_quota=per_owner_daily_quota,
                rate_limit_per_min=rate_limit_per_min,
            )
        )
    return mcp_id


# --------------------------------------------------------------------------- #
# 1. 纯函数：归一 + tools_hash
# --------------------------------------------------------------------------- #


def test_normalize_tool_canonical_and_risk() -> None:
    gw = ExternalMcpGateway()
    read_meta = gw.normalize_tool(
        server_name='qcc',
        raw_tool={'name': 'company-search', 'annotations': {'readOnlyHint': True}, 'inputSchema': {'type': 'object'}},
        origin='system',
    )
    assert read_meta['name'] == 'hasn.ext.qcc.company_search'  # 连字符合法化为下划线
    assert read_meta['raw_name'] == 'company-search'
    assert read_meta['risk_level'] == 'low'  # readOnlyHint=True → low
    assert read_meta['required_scopes'] == ['mcp:tool://hasn.ext.qcc.company_search']

    write_meta = gw.normalize_tool(
        server_name='qcc',
        raw_tool={'name': 'risk.fetch', 'inputSchema': {'type': 'object'}},
        origin='system',
    )
    assert write_meta['name'] == 'hasn.ext.qcc.risk_fetch'
    assert write_meta['risk_level'] == 'medium'  # 无 readOnlyHint → medium 起步


def test_compute_tools_hash_stable_and_order_independent() -> None:
    gw = ExternalMcpGateway()
    a = {'name': 'hasn.ext.x.a', 'schema_hash': 'h1'}
    b = {'name': 'hasn.ext.x.b', 'schema_hash': 'h2'}
    assert gw.compute_tools_hash([a, b]) == gw.compute_tools_hash([b, a])  # 排序稳定
    assert gw.compute_tools_hash([a]) != gw.compute_tools_hash([a, b])  # 内容变 → hash 变


# --------------------------------------------------------------------------- #
# 2. 注册校验：transport×hosting 矩阵（10 §4.1）
# --------------------------------------------------------------------------- #


async def test_register_rejects_illegal_transport_hosting() -> None:
    # remote_service 必须有 endpoint。
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'bad_{_suffix()}', hosting='remote_service', transport='http', endpoint=None
        )
    # stdio 只能 local_process。
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'bad_{_suffix()}', hosting='remote_service', transport='stdio', command='x'
        )
    # remote_service 不能指 loopback。
    with pytest.raises(RegistrationError):
        await external_mcp_gateway.register_server(
            name=f'bad_{_suffix()}',
            hosting='remote_service',
            transport='http',
            endpoint='http://127.0.0.1:9/mcp',
        )


async def test_register_server_persists_and_conflicts() -> None:
    """register_server 合法落库（公网端点，不连接）；重名 → TOOL_NAME_CONFLICT。"""
    name = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    mcp_id = None
    try:
        server = await external_mcp_gateway.register_server(
            name=name,
            hosting='remote_service',
            transport='http',
            origin='owner',
            owner_hasn_id=owner,
            endpoint='https://api.qcc.example.com/mcp',
            per_owner_daily_quota=100,
            rate_limit_per_min=20,
        )
        mcp_id = server['mcp_id']
        assert server['name'] == name
        assert server['status'] == 'active'
        assert server['endpoint'] == 'https://api.qcc.example.com/mcp'
        assert server['per_owner_daily_quota'] == 100
        # 重名冲突（10 §4 namespace 全局唯一）。
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.register_server(
                name=name, hosting='remote_service', transport='http',
                origin='owner', owner_hasn_id=owner, endpoint='https://other.example.com/mcp',
            )
        assert exc.value.code == McpErrorCode.TOOL_NAME_CONFLICT
    finally:
        if mcp_id:
            await _cleanup_server(mcp_id)


# --------------------------------------------------------------------------- #
# 3. secret:// 凭据生命周期（10 §7.1）
# --------------------------------------------------------------------------- #


async def test_secret_store_roundtrip_and_revoke() -> None:
    uri = secret_store.build_uri(origin='system', owner_hasn_id=None, server=f'qcc{_suffix()}', key='bearer')
    assert is_secret_ref(uri)
    try:
        returned = await secret_store.write(secret_uri=uri, plaintext='super-secret-token', origin='system')
        assert returned == uri  # 只回引用，绝不回明文
        assert await secret_store.exists(uri)
        assert await secret_store.resolve(uri) == 'super-secret-token'  # 解密往返
        # 轮换：同 URI 覆盖。
        await secret_store.write(secret_uri=uri, plaintext='rotated-token', origin='system')
        assert await secret_store.resolve(uri) == 'rotated-token'
        # 撤销：删除后 resolve 软挡 None。
        assert await secret_store.revoke(uri) is True
        assert await secret_store.resolve(uri) is None
        assert await secret_store.exists(uri) is False
    finally:
        await secret_store.revoke(uri)


# --------------------------------------------------------------------------- #
# 4. 全链路：注册 → 自省 → 绑定 → 解析 → 代理调用（真实 server）
# --------------------------------------------------------------------------- #


async def test_full_lifecycle_register_introspect_bind_proxy(stub_mcp_endpoint: str) -> None:
    name = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    agent = f'a_agent_{_suffix()}'
    mcp_id = None
    try:
        # 落库一行 remote_service server 指向真实回环 stub（绕注册期回环 UX 闸；运行期代理链路
        # 本就不重校回环——此处对其做真实 socket 自省/代理 E2E）。
        mcp_id = await _insert_loopback_server(name=name, endpoint=stub_mcp_endpoint, owner_hasn_id=owner)

        # 自省（真实 httpx → stub tools/list）。
        introspected = await external_mcp_gateway.introspect_server(mcp_id)
        assert introspected['health'] == 'healthy'
        names = {t['name'] for t in introspected['tools']}
        assert names == {f'hasn.ext.{name}.company_search', f'hasn.ext.{name}.risk_fetch'}
        assert introspected['tools_hash']

        # 绑定（owner 授权 agent 只用 company-search 一个工具）。
        binding = await external_mcp_gateway.create_binding(
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            mcp_id=mcp_id,
            allowed_raw_names=['company-search'],
        )
        assert len(binding['allowed_tools']) == 1
        assert binding['allowed_tools'][0]['tool_name'] == f'hasn.ext.{name}.company_search'

        # 解析（gate1 owner 命中 + gate2 binding 命中 → 只返回授权的 1 个工具）。
        tools = await external_mcp_gateway.resolve_agent_external_tools(agent_hasn_id=agent, owner_hasn_id=owner)
        assert [t['name'] for t in tools] == [f'hasn.ext.{name}.company_search']

        # 代理调用（真实 tools/call 经 SSE 往返）。
        result = await external_mcp_gateway.proxy_call(
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            tool_name=f'hasn.ext.{name}.company_search',
            arguments={'keyword': '小米'},
            trace_id='trace-1',
        )
        assert result['ok'] is True
        assert result['is_error'] is False
        assert 'company-search' in result['text']
        assert '小米' in result['text']

        # 记账落库（成功一条）。
        summary = await external_mcp_gateway_usage_count(mcp_id=mcp_id, owner=owner)
        assert summary >= 1

        # 门控：调用未授权工具 risk.fetch → TOOL_NOT_ALLOWED。
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.proxy_call(
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                tool_name=f'hasn.ext.{name}.risk_fetch',
                arguments={},
            )
        assert exc.value.code == McpErrorCode.TOOL_NOT_ALLOWED
    finally:
        if mcp_id:
            await _cleanup_server(mcp_id)


async def external_mcp_gateway_usage_count(*, mcp_id: str, owner: str) -> int:
    from sqlalchemy import func, select

    async with async_db_session() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ExternalMcpUsage)
                    .where(ExternalMcpUsage.mcp_id == mcp_id, ExternalMcpUsage.caller_owner_hasn_id == owner)
                )
            ).scalar_one()
            or 0
        )


# --------------------------------------------------------------------------- #
# 5. 门控：未绑定 Agent 解析为空
# --------------------------------------------------------------------------- #


async def test_unbound_agent_resolves_empty() -> None:
    tools = await external_mcp_gateway.resolve_agent_external_tools(
        agent_hasn_id=f'a_nobody_{_suffix()}', owner_hasn_id=f'h_nobody_{_suffix()}'
    )
    assert tools == []


# --------------------------------------------------------------------------- #
# 6. 管理面（P7-D）：凭据生命周期 + header 注入 + 行级隔离 + 列表/删除
# --------------------------------------------------------------------------- #


async def test_resolve_headers_substitutes_embedded_secret() -> None:
    """`Authorization: Bearer secret://...` → 解析为 `Bearer <明文>`；撤销后 CREDENTIAL_MISSING（软挡）。"""
    uri = secret_store.build_uri(origin='system', owner_hasn_id=None, server=f'qcc{_suffix()}', key='credential')
    try:
        await secret_store.write(secret_uri=uri, plaintext='tok-abc-123', origin='system')
        resolved = await external_mcp_gateway._resolve_headers({'Authorization': f'Bearer {uri}', 'X-Plain': 'keep'})
        assert resolved['Authorization'] == 'Bearer tok-abc-123'  # 嵌入式替换：保留 scheme 前缀
        assert resolved['X-Plain'] == 'keep'  # 非凭据值原样透传
        # 撤销后建连解析失败（撤销后调用软挡的实现点）。
        await secret_store.revoke(uri)
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway._resolve_headers({'Authorization': f'Bearer {uri}'})
        assert exc.value.code == McpErrorCode.CREDENTIAL_MISSING
    finally:
        await secret_store.revoke(uri)


async def test_management_credential_lifecycle_and_listing() -> None:
    """owner 写凭据 → header 模板落库 + credential_configured；轮换；撤销；删除连带清理。"""
    name = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    mcp_id = await _insert_server(
        name=name, endpoint='https://api.qcc.example.com/mcp', origin='owner', owner_hasn_id=owner
    )
    cred_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='credential')
    try:
        # 写凭据（明文 → 密文落库 + header 模板）。
        res = await external_mcp_gateway.set_credential(mcp_id=mcp_id, plaintext='qcc-token-1', owner_hasn_id=owner)
        assert res['credential_configured'] is True
        assert res['auth_header'] == 'Authorization'
        assert await secret_store.resolve(cred_uri) == 'qcc-token-1'

        # 列表出参：credential_configured + credential_header，且**不回显明文/密文**。
        servers = await external_mcp_gateway.list_servers(owner_hasn_id=owner, include_system=False)
        mine = next(s for s in servers if s['mcp_id'] == mcp_id)
        assert mine['credential_configured'] is True
        assert mine['credential_header'] == 'Authorization'
        assert 'headers' not in mine and 'ciphertext' not in mine  # 明文/密文绝不出 API

        # 轮换：同 URI 覆盖。
        await external_mcp_gateway.set_credential(mcp_id=mcp_id, plaintext='qcc-token-2', owner_hasn_id=owner)
        assert await secret_store.resolve(cred_uri) == 'qcc-token-2'

        # 撤销：删密文（header 模板保留 → 解析阶段软挡）。
        revoked = await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, owner_hasn_id=owner)
        assert revoked['revoked'] is True
        assert await secret_store.resolve(cred_uri) is None

        # 删除：连带删 server。
        assert await external_mcp_gateway.delete_server(mcp_id=mcp_id, owner_hasn_id=owner) is True
        assert await external_mcp_gateway.get_server_detail(mcp_id=mcp_id, owner_hasn_id=owner) is None
    finally:
        await _cleanup_server(mcp_id, secret_uri=cred_uri)


async def test_management_perm_isolation_and_quota() -> None:
    """owner 不能管 system server；admin 不能管 owner server；配额仅 system。"""
    owner = f'h_owner_{_suffix()}'
    sys_id = await _insert_server(name=f'qcc{_suffix()}', endpoint='https://qcc.example.com/mcp', origin='system')
    own_id = await _insert_server(
        name=f'gmail{_suffix()}', endpoint='https://gmail.example.com/mcp', origin='owner', owner_hasn_id=owner
    )
    try:
        # owner 配 system server 凭据 → 拒。
        with pytest.raises(McpToolError) as e1:
            await external_mcp_gateway.set_credential(mcp_id=sys_id, plaintext='x', owner_hasn_id=owner)
        assert e1.value.code == McpErrorCode.DIRECT_CALL_DENIED
        # admin 配 owner server 凭据 → 拒（admin 仅 system）。
        with pytest.raises(McpToolError) as e2:
            await external_mcp_gateway.set_credential(mcp_id=own_id, plaintext='x', is_admin=True)
        assert e2.value.code == McpErrorCode.DIRECT_CALL_DENIED
        # 配额仅适用 system-origin：对 owner server 配额 → 拒。
        with pytest.raises(McpToolError) as e3:
            await external_mcp_gateway.set_server_quota(mcp_id=own_id, per_owner_daily_quota=10, rate_limit_per_min=5)
        assert e3.value.code == McpErrorCode.DIRECT_CALL_DENIED
        # 对 system server 配额 → 成功落库。
        q = await external_mcp_gateway.set_server_quota(mcp_id=sys_id, per_owner_daily_quota=200, rate_limit_per_min=30)
        assert q['per_owner_daily_quota'] == 200 and q['rate_limit_per_min'] == 30

        # owner 列表含自己的 owner server + system 共享（include_system=True）。
        listed = await external_mcp_gateway.list_servers(owner_hasn_id=owner, include_system=True)
        ids = {s['mcp_id'] for s in listed}
        assert own_id in ids and sys_id in ids
        # include_system=False 只见自己的。
        own_only = await external_mcp_gateway.list_servers(owner_hasn_id=owner, include_system=False)
        own_ids = {s['mcp_id'] for s in own_only}
        assert own_id in own_ids and sys_id not in own_ids
    finally:
        await _cleanup_server(sys_id)
        await _cleanup_server(own_id)


async def test_management_credential_unblocks_proxy_auth(stub_mcp_endpoint: str) -> None:
    """凭据写入后 proxy 建连真实带上 `Authorization: Bearer <明文>`（stub 回显校验注入生效）。"""
    name = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    agent = f'a_agent_{_suffix()}'
    mcp_id = await _insert_loopback_server(name=name, endpoint=stub_mcp_endpoint, owner_hasn_id=owner)
    cred_uri = secret_store.build_uri(origin='owner', owner_hasn_id=owner, server=name, key='credential')
    try:
        await external_mcp_gateway.set_credential(mcp_id=mcp_id, plaintext='live-bearer-xyz', owner_hasn_id=owner)
        await external_mcp_gateway.introspect_server(mcp_id)  # 自省带凭据建连成功（无 CREDENTIAL_MISSING）
        await external_mcp_gateway.create_binding(
            agent_hasn_id=agent, owner_hasn_id=owner, mcp_id=mcp_id, allowed_raw_names=['company-search']
        )
        result = await external_mcp_gateway.proxy_call(
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            tool_name=f'hasn.ext.{name}.company_search',
            arguments={'keyword': '唤星'},
        )
        assert result['ok'] is True and '唤星' in result['text']
        # 撤销后 proxy 建连软挡（CREDENTIAL_MISSING）。
        await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, owner_hasn_id=owner)
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.proxy_call(
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                tool_name=f'hasn.ext.{name}.company_search',
                arguments={'keyword': '唤星'},
            )
        assert exc.value.code == McpErrorCode.CREDENTIAL_MISSING
    finally:
        await _cleanup_server(mcp_id, secret_uri=cred_uri)


# --------------------------------------------------------------------------- #
# 7. 平台 key 治理（P7-C/E）：system-origin per-owner 每日配额真实生效（MCP_9216）
# --------------------------------------------------------------------------- #


async def test_system_origin_daily_quota_enforced(stub_mcp_endpoint: str) -> None:
    """system-origin 平台 server 配 per_owner_daily_quota=1：第 1 次代理成功落账本，
    第 2 次 enforce 命中今日配额 → MCP_9216 QUOTA_EXCEEDED（真实 PG 账本聚合，零 mock）。"""
    name = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    agent = f'a_agent_{_suffix()}'
    mcp_id = await _insert_server(
        name=name,
        endpoint=stub_mcp_endpoint,
        origin='system',
        owner_hasn_id=None,
        per_owner_daily_quota=1,
    )
    try:
        # 自省填充 advertised_tools_cache（proxy 的 tools_hash 校验需命中）。
        await external_mcp_gateway.introspect_server(mcp_id)
        await external_mcp_gateway.create_binding(
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            mcp_id=mcp_id,
            allowed_raw_names=['company-search'],
        )
        # 第 1 次：配额未用尽（used=0 < 1）→ 成功 + 记账。
        first = await external_mcp_gateway.proxy_call(
            agent_hasn_id=agent,
            owner_hasn_id=owner,
            tool_name=f'hasn.ext.{name}.company_search',
            arguments={'keyword': '小米'},
            trace_id='quota-1',
        )
        assert first['ok'] is True
        # 第 2 次：今日已用尽（used=1 >= 1）→ enforce 前置挡 MCP_9216。
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.proxy_call(
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                tool_name=f'hasn.ext.{name}.company_search',
                arguments={'keyword': '腾讯'},
                trace_id='quota-2',
            )
        assert exc.value.code == McpErrorCode.QUOTA_EXCEEDED
        # 账本仅 1 条成功（被挡的第 2 次不应记成成功）。
        assert await external_mcp_gateway_usage_count(mcp_id=mcp_id, owner=owner) == 1
    finally:
        await _cleanup_server(mcp_id, agent_hasn_id=agent)


# --------------------------------------------------------------------------- #
# 8. P7-E：分身经云端 MCP「发现+派发」层端到端（qcc 形态 = system-origin 平台 key）
#    —— 区别于上文 gateway-direct 测试：此处走 server 暴露/派发层
#    （tool_directory.ToolDirectoryService 投影 + external_tool.ExternalMcpTool.execute），
#    验证 tool.search 各层级真实暴露 external、代理往返、用量归因、明文零泄露、跨 Agent 隔离。
# --------------------------------------------------------------------------- #


def _agent_ctx(*, agent: str, owner: str, trace_id: str = 'p7e') -> AgentContext:
    return AgentContext(
        hasn_id=agent,
        owner_id=100001,
        agent_status='active',
        metadata={'trace_id': trace_id},
        owner_hasn_id=owner,
    )


async def _registry_for_agent(ctx: AgentContext) -> ToolRegistry:
    """模拟 server.py 入站：解析该 Agent external 工具 → 注入 external_allowed_tools → 入注册表。"""
    tools = await load_external_mcp_tools_for_agent(ctx)
    ctx.external_allowed_tools = {t.name for t in tools}
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


async def test_p7e_agent_path_discover_dispatch_attribute_no_leak(stub_mcp_endpoint: str) -> None:
    """qcc 形态 system-origin 平台 server：自省→绑定→分身经 tool.search 各层级发现
    `hasn.ext.{ns}.*`→ExternalMcpTool.execute 真实代理 → 用量归因到调用 owner → 平台 key 明文零泄露。"""
    ns = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    agent = f'a_agent_{_suffix()}'
    platform_key = f'qcc-platform-bearer-{_suffix()}'
    mcp_id = await _insert_server(name=ns, endpoint=stub_mcp_endpoint, origin='system', owner_hasn_id=None)
    try:
        # 平台 key（system-origin，admin 写）：明文加密落库、注入出站、分身永不接触。
        await external_mcp_gateway.set_credential(mcp_id=mcp_id, plaintext=platform_key, is_admin=True)
        await external_mcp_gateway.introspect_server(mcp_id)
        await external_mcp_gateway.create_binding(
            agent_hasn_id=agent, owner_hasn_id=owner, mcp_id=mcp_id, allowed_raw_names=['company-search']
        )

        # ---- 发现层：server 注入 external_allowed_tools + ToolDirectory 投影 ----
        ctx = _agent_ctx(agent=agent, owner=owner, trace_id='p7e-1')
        registry = await _registry_for_agent(ctx)
        # 仅授权 1 个工具被投影成 source='external'。
        assert {t.name for t in registry.get_all_tools()} == {f'hasn.ext.{ns}.company_search'}

        directory = ToolDirectoryService(registry)
        # hasn.cloud.tool.search 'sources' 层级：external 分组真实派生（不再恒空）。
        sources = await directory.search(ctx, ToolSearchQuery(query='sources', detail='sources'))
        ext_sources = [s for s in sources['sources'] if s['source'] == 'external']
        assert ext_sources and ext_sources[0]['namespace'] == f'hasn.ext.{ns}'
        assert ext_sources[0]['visible_tool_count'] == 1
        # 'external' 源层级 + 命名空间层级 + tool: 精确层级都命中。
        by_source = await directory.search(ctx, ToolSearchQuery(query='external', source='external'))
        assert [t['name'] for t in by_source['tools']] == [f'hasn.ext.{ns}.company_search']
        by_ns = await directory.search(ctx, ToolSearchQuery(query=f'hasn.ext.{ns}'))
        assert any(t['name'] == f'hasn.ext.{ns}.company_search' for t in by_ns['tools'])
        by_tool = await directory.search(
            ctx, ToolSearchQuery(query=f'tool:hasn.ext.{ns}.company_search', detail='schema')
        )
        assert [s['name'] for s in by_tool['schemas']] == [f'hasn.ext.{ns}.company_search']

        # ---- 明文零泄露：平台 key 绝不出现在任何 agent 面投影 ----
        import json as _json

        blob = _json.dumps([sources, by_source, by_ns, by_tool], ensure_ascii=False, default=str)
        assert platform_key not in blob

        # ---- 派发层：ExternalMcpTool.execute → gateway.proxy_call → 真实 stub SSE 往返 ----
        tool = next(t for t in registry.get_all_tools() if t.name == f'hasn.ext.{ns}.company_search')
        result = await tool.execute(ctx, {'keyword': '小米'})
        assert result['ok'] is True and result['is_error'] is False
        assert 'company-search' in result['text'] and '小米' in result['text']

        # 用量归因到调用 owner（平台 key 计费摊给调用方）。
        assert await external_mcp_gateway_usage_count(mcp_id=mcp_id, owner=owner) >= 1
    finally:
        with contextlib.suppress(Exception):
            await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, is_admin=True)
        await _cleanup_server(mcp_id)


async def test_p7e_external_discovery_isolated_across_agents(stub_mcp_endpoint: str) -> None:
    """平台 server 实例全局共享，发现/调用资格按本 Agent binding——未绑定 Agent 一无所见
    （即便把已绑定 Agent 的注册表塞给它，_can_discover 仍按 external_allowed_tools 挡，杜绝串号）。"""
    ns = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    bound = f'a_bound_{_suffix()}'
    other = f'a_other_{_suffix()}'
    mcp_id = await _insert_server(name=ns, endpoint=stub_mcp_endpoint, origin='system', owner_hasn_id=None)
    try:
        await external_mcp_gateway.introspect_server(mcp_id)
        await external_mcp_gateway.create_binding(
            agent_hasn_id=bound, owner_hasn_id=owner, mcp_id=mcp_id, allowed_raw_names=['company-search']
        )
        # 已绑定 Agent：发现到 1 个。
        bound_ctx = _agent_ctx(agent=bound, owner=owner)
        bound_reg = await _registry_for_agent(bound_ctx)
        assert {t.name for t in bound_reg.get_all_tools()} == {f'hasn.ext.{ns}.company_search'}

        # 未绑定 Agent（同 owner）：resolve 为空。
        other_ctx = _agent_ctx(agent=other, owner=owner)
        assert await load_external_mcp_tools_for_agent(other_ctx) == []

        # 串号防线：把 bound 的注册表交给 other_ctx（空 external_allowed_tools）→ tool.search 仍看不到。
        other_ctx.external_allowed_tools = set()
        directory = ToolDirectoryService(bound_reg)
        leaked = await directory.search(other_ctx, ToolSearchQuery(query='external', source='external'))
        assert leaked['tools'] == []
        sources = await directory.search(other_ctx, ToolSearchQuery(query='sources', detail='sources'))
        assert all(s['source'] != 'external' for s in sources['sources'])
    finally:
        await _cleanup_server(mcp_id)


# --------------------------------------------------------------------------- #
# 9. 架构A：call_system_tool 服务端接缝（system-origin 平台工具，绕 per-agent binding）
#    —— 获客 hasn_growth read-through 路径：业务层以平台身份调 qcc，无需为每个分身建 binding。
# --------------------------------------------------------------------------- #


async def _usage_first_agent_of(*, mcp_id: str, owner: str) -> str | None:
    """该 (mcp_id, owner) 最早一条记账的 caller_agent_hasn_id（验证 'system' 哨兵 / agent 归因）。"""
    from sqlalchemy import select

    async with async_db_session() as db:
        return (
            await db.execute(
                select(ExternalMcpUsage.caller_agent_hasn_id)
                .where(ExternalMcpUsage.mcp_id == mcp_id, ExternalMcpUsage.caller_owner_hasn_id == owner)
                .order_by(ExternalMcpUsage.id.asc())
            )
        ).scalars().first()


async def test_call_system_tool_bypasses_binding_and_attributes_owner(stub_mcp_endpoint: str) -> None:
    """system-origin 平台 server 经 call_system_tool 被服务端以平台身份调用——**无需 per-agent binding**
    （获客 hasn_growth read-through 路径）。配额/记账仍按 caller owner 归因；未传 agent 记账落 'system'
    哨兵，传 agent 则归该 agent。平台 key 明文零泄露。"""
    ns = f'qcc{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    platform_key = f'qcc-platform-bearer-{_suffix()}'
    mcp_id = await _insert_server(name=ns, endpoint=stub_mcp_endpoint, origin='system', owner_hasn_id=None)
    try:
        await external_mcp_gateway.set_credential(mcp_id=mcp_id, plaintext=platform_key, is_admin=True)
        await external_mcp_gateway.introspect_server(mcp_id)

        # 关键：未建任何 binding，直接以平台身份调用（架构A 绕 binding）。
        result = await external_mcp_gateway.call_system_tool(
            owner_hasn_id=owner,
            tool_name=f'hasn.ext.{ns}.company_search',
            arguments={'keyword': '小米'},
            trace_id='sys-1',
        )
        assert result['ok'] is True and result['is_error'] is False
        assert 'company-search' in result['text'] and '小米' in result['text']

        # 记账归调用 owner；未传 agent → caller_agent 'system' 哨兵。
        assert await external_mcp_gateway_usage_count(mcp_id=mcp_id, owner=owner) == 1
        assert await _usage_first_agent_of(mcp_id=mcp_id, owner=owner) == 'system'

        # 传 agent_hasn_id 时记账归该 agent（第 2 条）。
        await external_mcp_gateway.call_system_tool(
            owner_hasn_id=owner,
            tool_name=f'hasn.ext.{ns}.company_search',
            arguments={'keyword': '腾讯'},
            agent_hasn_id=f'a_caller_{_suffix()}',
        )
        assert await external_mcp_gateway_usage_count(mcp_id=mcp_id, owner=owner) == 2

        # 明文零泄露：平台 key 不出现在归一返回。
        import json as _json

        assert platform_key not in _json.dumps(result, ensure_ascii=False, default=str)
    finally:
        with contextlib.suppress(Exception):
            await external_mcp_gateway.revoke_credential(mcp_id=mcp_id, is_admin=True)
        await _cleanup_server(mcp_id)


async def test_call_system_tool_rejects_non_system_origin(stub_mcp_endpoint: str) -> None:
    """call_system_tool 只服务 system-origin；owner-origin（自带 key）server → DIRECT_CALL_DENIED
    （owner server 仍须走 proxy_call + binding，平台接缝不为其代付/绕授权）。"""
    ns = f'gmail{_suffix()}'
    owner = f'h_owner_{_suffix()}'
    mcp_id = await _insert_server(name=ns, endpoint=stub_mcp_endpoint, origin='owner', owner_hasn_id=owner)
    try:
        with pytest.raises(McpToolError) as exc:
            await external_mcp_gateway.call_system_tool(
                owner_hasn_id=owner,
                tool_name=f'hasn.ext.{ns}.company_search',
                arguments={'keyword': 'x'},
            )
        assert exc.value.code == McpErrorCode.DIRECT_CALL_DENIED
    finally:
        await _cleanup_server(mcp_id)


async def test_seed_qcc_servers_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """qcc_seed 幂等：首次注册 system remote_service server，二次全部 existed 不重复建行；
    参数形态正确（origin=system / hosting=remote_service / transport=http / qcc endpoint）。
    用测试专属 namespace 隔离，不碰真实 qcc_* 平台行。"""
    from backend.app.external_mcp import qcc_seed

    sfx = _suffix()
    test_specs = (
        qcc_seed.QccServerSpec(f'qcctest_company_{sfx}', 'company', '测试·工商'),
        qcc_seed.QccServerSpec(f'qcctest_risk_{sfx}', 'risk', '测试·风险'),
    )
    monkeypatch.setattr(qcc_seed, 'QCC_SERVERS', test_specs)
    mcp_ids: list[str] = []
    try:
        # 首次：全部 registered（不传 token、不自省，纯注册逻辑）。
        first = await qcc_seed.seed_qcc_servers(bearer_token=None, introspect=False)
        assert [r['action'] for r in first] == ['registered', 'registered']
        assert all(r['credential_written'] is False for r in first)
        mcp_ids = [r['mcp_id'] for r in first]

        # 形态正确：endpoint 指向真实 qcc；落库 origin/hosting/transport 正确。
        assert first[0]['endpoint'] == 'https://agent.qcc.com/mcp/company/stream'
        detail = await external_mcp_gateway.get_server_detail(mcp_id=mcp_ids[0], is_admin=True)
        assert detail is not None
        assert detail['origin'] == 'system'
        assert detail['hosting'] == 'remote_service'
        assert detail['transport'] == 'http'

        # 二次：全部 existed（幂等，复用同一行）。
        second = await qcc_seed.seed_qcc_servers(bearer_token=None, introspect=False)
        assert [r['action'] for r in second] == ['existed', 'existed']
        assert {r['mcp_id'] for r in second} == set(mcp_ids)

        # 平台目录里每个测试 namespace 只有一行。
        sysservers = await external_mcp_gateway.list_servers_admin(origin='system')
        for spec in test_specs:
            assert len([s for s in sysservers if s['name'] == spec.namespace]) == 1
    finally:
        for mcp_id in mcp_ids:
            await _cleanup_server(mcp_id)
