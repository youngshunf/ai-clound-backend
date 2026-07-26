"""项目管理路由面安全守卫。

只允许 Owner App API 进入主路由；分身通过 ``hasn.project.*`` MCP 工具调用，
不得重新挂载 codegen 的公开、Agent 或硬删除 CRUD 路由。
"""

from fastapi.routing import APIRoute

from backend.app.router import router
from backend.app.mcp.tools.project import PROJECT_TOOLS
from backend.common.security.jwt import DependsJwtAuth


def _project_routes() -> list[APIRoute]:
    """取已装载主路由中的项目管理 HTTP 端点。"""
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.startswith('/api/v1/project/')
    ]


def test_project_main_router_exposes_only_owner_app_http_surface() -> None:
    """公开、Agent 裸 CRUD 与任何项目硬删除均不得进入 OpenAPI 主路由。"""
    routes = _project_routes()
    paths = {route.path for route in routes}

    assert routes, '项目 Owner App 路由必须装载'
    assert all('/open/' not in path for path in paths)
    assert all('/agent/' not in path for path in paths)
    assert all('DELETE' not in route.methods for route in routes)


def test_project_owner_app_routes_keep_owner_jwt_dependency() -> None:
    """项目 Owner App 面仍必须经 Owner JWT 认证，不得因路由收口而降级。"""
    owner_routes = [route for route in _project_routes() if '/app/' in route.path]

    assert owner_routes, '项目 Owner App 路由必须保留'
    for route in owner_routes:
        assert any(dependency.dependency is DependsJwtAuth.dependency for dependency in route.dependencies)


def test_project_inspection_publish_is_a_scoped_platform_tool() -> None:
    """项目经理只能经规范平台工具发布巡检建议，不能经裸 Agent HTTP CRUD 写入。"""
    tool = next(tool for tool in PROJECT_TOOLS if tool.name == 'hasn.project.inspection.publish')

    assert tool.source == 'platform'
    assert tool.execution_location == 'cloud'
    assert tool.required_scopes == ['project:write']
    assert set(tool.input_schema['required']) == {'project_id', 'fingerprint', 'suggestion'}
