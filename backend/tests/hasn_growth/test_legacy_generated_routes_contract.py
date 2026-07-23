"""获客遗留生成式路由的注册契约。"""

import pytest

from fastapi import APIRouter

from backend.app.hasn_growth.api.router import app


@pytest.mark.parametrize(
    'legacy_path',
    (
        '/api/v1/growth/app/lead-source-configs',
        '/api/v1/growth/app/lead/firecrawl/requests',
        '/api/v1/growth/app/lead/raw/records',
        '/api/v1/growth/app/lead/contact/sources',
        '/api/v1/growth/app/lead/rejected/records',
        '/api/v1/growth/app/lead/export/items',
        '/api/v1/growth/app/lead/audit/logs',
    ),
)
def test_growth_app_router_does_not_register_unscoped_generated_crud(legacy_path: str) -> None:
    """没有主人隔离契约的生成式 CRUD 路由不得对外注册。"""
    router: APIRouter = app

    assert legacy_path not in {getattr(route, 'path', '') for route in router.routes}
