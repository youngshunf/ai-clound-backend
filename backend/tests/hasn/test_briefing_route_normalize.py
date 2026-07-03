"""publish 工具写入前的 legacy 路由归一化（纯函数，无 DB）。

对齐 webui `src/lib/legacyRoute.ts`：/workbench 前缀去除 + 裸应用根段补 /apps，
让存库 BriefingDocument 的 deep_link/route 本身 canonical（旧简报按钮不再 404）。
"""

from __future__ import annotations

from backend.app.mcp.tools.workbench import _canonicalize_document_routes, _normalize_route


def test_normalize_strips_workbench_apps_prefix() -> None:
    assert _normalize_route('/workbench/apps/deck') == '/apps/deck'
    assert _normalize_route('/workbench/apps/knowledge/docs/1234') == '/apps/knowledge/docs/1234'


def test_normalize_bare_app_root_gets_apps_prefix() -> None:
    assert _normalize_route('/tasks/T-12') == '/apps/tasks/T-12'
    assert _normalize_route('/community/posts/p_1') == '/apps/community/posts/p_1'
    assert _normalize_route('/plan') == '/apps/plan'


def test_normalize_bare_workbench_goes_home() -> None:
    assert _normalize_route('/workbench') == '/home'
    assert _normalize_route('/workbench/deck/deck_1') == '/apps/deck/deck_1'


def test_normalize_preserves_query_hash() -> None:
    assert _normalize_route('/workbench/apps/tasks?tab=failed#top') == '/apps/tasks?tab=failed#top'


def test_normalize_leaves_canonical_and_toplevel_untouched() -> None:
    assert _normalize_route('/apps/deck') == '/apps/deck'
    assert _normalize_route('/messages/c/conv_1') == '/messages/c/conv_1'
    assert _normalize_route('/workflows/wf_1') == '/workflows/wf_1'
    assert _normalize_route('hasn://deck/deck_1') == 'hasn://deck/deck_1'
    assert _normalize_route('https://x.com/workbench/apps/deck') == 'https://x.com/workbench/apps/deck'
    assert _normalize_route(None) is None


def test_canonicalize_document_walks_actions_and_source() -> None:
    document = {
        'summary': 's',
        'focus_items': [
            {
                'item_id': 'fi_1',
                'category': 'app',
                'urgency': 'high',
                'title': 't',
                'source': {'app_id': 'deck', 'deep_link': '/workbench/apps/deck'},
                'actions': [
                    {'kind': 'open_app', 'label': 'a', 'deep_link': '/workbench/apps/knowledge/docs/9'},
                    {'kind': 'open_route', 'label': 'b', 'route': '/tasks/T-1'},
                    {'kind': 'run_task', 'label': 'c', 'prompt': 'do it'},
                ],
            }
        ],
        'plans': [
            {
                'plan_id': 'pl_1',
                'title': 'p',
                'horizon': 'today',
                'actions': [{'kind': 'open_route', 'label': 'x', 'route': '/workbench/apps/plan'}],
            }
        ],
    }
    out = _canonicalize_document_routes(document)
    fi = out['focus_items'][0]
    assert fi['source']['deep_link'] == '/apps/deck'
    assert fi['actions'][0]['deep_link'] == '/apps/knowledge/docs/9'
    assert fi['actions'][1]['route'] == '/apps/tasks/T-1'
    assert 'deep_link' not in fi['actions'][2]  # run_task 无路由字段不误加
    assert out['plans'][0]['actions'][0]['route'] == '/apps/plan'
    # 不改入参（immutable）。
    assert document['focus_items'][0]['source']['deep_link'] == '/workbench/apps/deck'
