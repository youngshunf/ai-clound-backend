"""路由依赖契约回归（防 from __future__ import annotations + flake8-type-checking 误伤）。

历史坑：API 端点文件若启用 `from __future__ import annotations` 并被自动 lint 把
CurrentSession / AgentTokenPayload 移进 TYPE_CHECKING，FastAPI 运行时无法解析依赖，
会把 db/agent/body 静默降级为 query 参数（端点静默损坏，import 仍成功）。
本测试锁定：通知路由的 db 永远是依赖（绝不出现在 query），且 Agent emit 带 JWT 鉴权。
"""
from __future__ import annotations

from fastapi.routing import APIRoute

import backend.app.router as app_router


def _notification_routes() -> list[APIRoute]:
    return [
        r
        for r in app_router.router.routes
        if isinstance(r, APIRoute) and '/api/v1/notifications/' in r.path
    ]


def test_db_never_leaks_to_query_params():
    routes = _notification_routes()
    assert routes, '应至少注册若干 /api/v1/notifications/* 路由'
    for route in routes:
        qp = {p.name for p in route.dependant.query_params}
        assert 'db' not in qp, f'{route.path} 的 db 泄漏成 query 参数（依赖未解析）'
        assert 'agent' not in qp, f'{route.path} 的 agent 泄漏成 query 参数（JWT 依赖未解析）'


def test_owner_routes_have_session_dependency():
    for route in _notification_routes():
        if '/notifications/app/' not in route.path:
            continue
        subdeps = {getattr(d.call, '__name__', '') for d in route.dependant.dependencies}
        assert subdeps & {'get_db', 'get_db_transaction'}, f'{route.path} 缺少 DB session 依赖'


def test_agent_emit_requires_jwt_and_body():
    emit = next(
        (r for r in _notification_routes() if r.path.endswith('/agent/notifications/emit')),
        None,
    )
    assert emit is not None, '应注册 Agent emit 路由'
    subdeps = {getattr(d.call, '__name__', '') for d in emit.dependant.dependencies}
    assert 'agent_jwt_auth' in subdeps, 'Agent emit 必须经 Agent JWT 鉴权'
    body = {p.name for p in emit.dependant.body_params}
    assert 'body' in body, 'Agent emit 必须解析请求体'


def test_app_emit_requires_jwt_and_body():
    """AI-Native App emit（§7/P5）路由必须经 Agent JWT 鉴权且解析请求体。"""
    app_emit = next(
        (r for r in _notification_routes() if r.path.endswith('/agent/notifications/app-emit')),
        None,
    )
    assert app_emit is not None, '应注册 App emit 路由'
    subdeps = {getattr(d.call, '__name__', '') for d in app_emit.dependant.dependencies}
    assert 'agent_jwt_auth' in subdeps, 'App emit 必须经 Agent JWT 鉴权'
    body = {p.name for p in app_emit.dependant.body_params}
    assert 'body' in body, 'App emit 必须解析请求体'
