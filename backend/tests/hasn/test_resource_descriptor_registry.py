"""RC-P0 资源描述符契约固化测试（doc31 §2，实施/32 RC-P0）。

纯 registry 逻辑，不依赖 DB：校验 manifest.resources[] 解析、越界拒绝、descriptor 查询、
资源路由投影。deck 是首个声明的资源（native_window），作验收样例。
"""

from __future__ import annotations

import copy

import pytest

from backend.app.hasn.schema.resource_descriptor import ResourceDescriptor
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry


def test_deck_manifest_declares_resource_descriptor() -> None:
    """deck manifest 声明了 resources[]，且能被 registry 查到（RC-P0 验收）。"""
    descriptor = ai_native_app_registry.resource_descriptor('deck')
    assert descriptor is not None
    assert descriptor.resource_kind == 'deck.presentation'
    assert descriptor.uri_domain == 'deck'
    assert descriptor.open.mode == 'native_window'
    assert descriptor.open.window == 'deck'
    assert descriptor.card.verb == '演示文稿'
    assert descriptor.card.action_label == '打开演示文稿'
    assert descriptor.artifact_kind == 'deck'


def test_resource_routes_projects_deck_route() -> None:
    """resource_routes() 投影出 deck 的扁平资源路由（native_window→window=deck）。"""
    routes = ai_native_app_registry.resource_routes()
    deck_routes = [r for r in routes if r.app_id == 'deck']
    assert len(deck_routes) == 1
    route = deck_routes[0]
    assert route.uri_domain == 'deck'
    assert route.open_mode == 'native_window'
    assert route.window == 'deck'
    assert route.route_template is None


def test_deck_manifest_passes_validation_with_resources() -> None:
    """含 resources[] 的 deck manifest 通过校验，不产出 resource_descriptor_invalid 错误。"""
    manifest = ai_native_app_registry.get_builtin_manifest('deck')
    result = ai_native_app_registry.validate_manifest(manifest)
    resource_errors = [e for e in result.errors if e.startswith('resource_descriptor_invalid')]
    assert resource_errors == [], f'deck resources 校验失败: {result.errors}'


def test_validate_manifest_rejects_invalid_descriptor() -> None:
    """越界 descriptor（internal_route 缺 route_template）被校验拒绝。"""
    manifest = copy.deepcopy(ai_native_app_registry.get_builtin_manifest('deck'))
    manifest['resources'] = [
        {
            'resource_kind': 'bad.kind',
            'uri_domain': 'bad',
            'open': {'mode': 'internal_route'},  # 缺 route_template
            'card': {'verb': '坏资源', 'action_label': '打开'},
        }
    ]
    result = ai_native_app_registry.validate_manifest(manifest)
    assert any(e.startswith('resource_descriptor_invalid') for e in result.errors)


def test_descriptor_schema_validation_rules() -> None:
    """ResourceDescriptor 各 open.mode 的必填字段校验（纯 schema 层）。"""
    # native_window 缺 window → 拒绝
    with pytest.raises(Exception):
        ResourceDescriptor.model_validate({
            'resource_kind': 'x',
            'uri_domain': 'x',
            'open': {'mode': 'native_window'},
            'card': {'verb': 'a', 'action_label': 'b'},
        })
    # internal_route route_template 无 :id → 拒绝
    with pytest.raises(Exception):
        ResourceDescriptor.model_validate({
            'resource_kind': 'x',
            'uri_domain': 'reel/projects',
            'open': {'mode': 'internal_route', 'route_template': '/apps/reel/projects'},
            'card': {'verb': 'a', 'action_label': 'b'},
        })
    # uri_domain 含 hasn:// → 拒绝
    with pytest.raises(Exception):
        ResourceDescriptor.model_validate({
            'resource_kind': 'x',
            'uri_domain': 'hasn://reel',
            'open': {'mode': 'internal_route', 'route_template': '/apps/reel/:id'},
            'card': {'verb': 'a', 'action_label': 'b'},
        })
    # card.verb 空 → 拒绝
    with pytest.raises(Exception):
        ResourceDescriptor.model_validate({
            'resource_kind': 'x',
            'uri_domain': 'reel/projects',
            'open': {'mode': 'internal_route', 'route_template': '/apps/reel/:id'},
            'card': {'verb': '  ', 'action_label': 'b'},
        })
    # entry_query 全字段 → 通过
    ok = ResourceDescriptor.model_validate({
        'resource_kind': 'imagelab.project',
        'uri_domain': 'imagelab/projects',
        'open': {'mode': 'entry_query', 'entry_route': '/apps/imagelab', 'query_key': 'item'},
        'card': {'verb': '图像', 'action_label': '打开图坊'},
    })
    assert ok.open.entry_route == '/apps/imagelab'
    assert ok.open.query_key == 'item'
