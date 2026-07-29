"""会议增强候选 revision 的 Owner API 契约测试。"""

from backend.app.hasn_copilot.api.v1.app.meetings import router


def test_revision_routes_are_owner_scoped_and_dedicated() -> None:
    routes = {(method, route.path) for route in router.routes for method in getattr(route, 'methods', set())}
    base = '/meetings/{meeting_id}/enhancement-revisions'
    assert ('POST', base) in routes
    assert ('GET', base) in routes
    assert ('POST', f'{base}/{{revision_id}}/accept') in routes
    assert ('POST', f'{base}/{{revision_id}}/reject') in routes
