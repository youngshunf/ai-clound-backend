"""HASN 遗留生成式路由的注册契约。"""

import pytest

from backend.app.hasn.api.router import agent, app


@pytest.mark.parametrize(
    ('router', 'legacy_path'),
    (
        (agent, '/api/v1/hasn/agent/notifications'),
        (agent, '/api/v1/hasn/agent/unread/counts'),
        (agent, '/api/v1/hasn/agent/trade/sessions'),
        (app, '/api/v1/hasn/app/notifications'),
        (app, '/api/v1/hasn/app/unread/counts'),
        (app, '/api/v1/hasn/app/trade/sessions'),
    ),
)
def test_router_does_not_register_legacy_generated_crud(router: object, legacy_path: str) -> None:
    """无资源归属契约的生成式 CRUD 路由不得对外注册。"""
    route_paths = {route.path for route in router.routes}  # type: ignore[attr-defined]

    assert legacy_path not in route_paths


@pytest.mark.parametrize(
    ('router', 'required_path'),
    (
        (agent, '/api/v1/hasn/agent/agents/by-hasn-id/{hasn_id}/heartbeat'),
        (app, '/api/v1/hasn/app/agents/by-hasn-id/{hasn_id}/runtime-config'),
        (app, '/api/v1/hasn/app/conversations/messages:sync'),
        (app, '/api/v1/hasn/app/conversations/ensure'),
    ),
)
def test_router_preserves_scoped_business_endpoints(router: object, required_path: str) -> None:
    """清理通用路由不得影响有明确身份边界的业务端点。"""
    route_paths = {route.path for route in router.routes}  # type: ignore[attr-defined]

    assert required_path in route_paths
