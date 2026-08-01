"""技能市场 Agent Interface 路由名称契约。"""

from fastapi import FastAPI

from backend.app.marketplace.api.router import agent, app, open_api, publish
from backend.utils.openapi import ensure_unique_route_names


def test_agent_interface_route_names_do_not_collide_with_existing_marketplace_routes() -> None:
    """Agent 新接口不得与 Owner、公开或旧发布接口复用 OpenAPI operation ID。"""
    application = FastAPI()
    application.include_router(publish)
    application.include_router(app)
    application.include_router(agent)
    application.include_router(open_api)

    ensure_unique_route_names(application)
