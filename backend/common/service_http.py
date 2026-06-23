"""service_http —— 内部独立服务（finance / quant 等）HTTP 通信的进程级单例连接池。

每次请求新建 ``httpx.AsyncClient`` 会重做 TCP(+TLS) 握手、连接池用完即弃，内网高频调用浪费大；
本模块按名字缓存**进程级单例** client，keep-alive 连接在请求间复用（连接池命中）。

``http2=True`` 是 best-effort：走 TLS 且对端 ALPN 协商出 h2 时升级 HTTP/2，否则（如内网
cleartext ``http://`` + uvicorn 只讲 HTTP/1.1）自动留在 HTTP/1.1，不影响正确性——已实测
cleartext + 双版本启用下稳定回落 HTTP/1.1，不会触发 h2c prior-knowledge 而打挂 HTTP/1.1 服务。

超时**不在此固定**——由各 provider 调用时 ``client.post(..., timeout=...)`` per-request 传入，
保留各服务 ``*_TIMEOUT`` 配置语义。进程退出时 lifespan 调 :func:`close_service_http_clients`
优雅关闭所有池（见 ``backend/core/registrar.py``）。

注意：
- 多 worker 部署下每个 worker 进程各持一份池（符合预期，连接池本就是 per-process）。
- asyncio 单线程协作调度，:func:`get_service_client` 内无 ``await``，check-and-create 不会交错，无需锁。
- ``trust_env=False``：内网服务直连，不经系统代理（与 newapi / asset 内部 client 一致）。
"""

from __future__ import annotations

import httpx

# 连接池上限：内网服务间调用以保活连接复用为主；上限给足并发突发但不无界。
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)

_clients: dict[str, httpx.AsyncClient] = {}


def get_service_client(name: str) -> httpx.AsyncClient:
    """取（或惰性建）名为 ``name`` 的进程级单例 ``httpx.AsyncClient``（连接池 + HTTP/2 best-effort）。

    :param name: 池名（每个独立服务一个，如 ``'finance'`` / ``'quant'``），按名隔离连接池。
    :return: 单例 client；**调用方不要 ``async with`` / ``aclose()``**（由 lifespan 统一关闭）。
        超时每次调用 per-request 传入。
    """
    client = _clients.get(name)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(http2=True, limits=_DEFAULT_LIMITS, trust_env=False)
        _clients[name] = client
    return client


async def close_service_http_clients() -> None:
    """进程退出时优雅关闭所有连接池（FastAPI lifespan 收尾调用）。"""
    for client in list(_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _clients.clear()
