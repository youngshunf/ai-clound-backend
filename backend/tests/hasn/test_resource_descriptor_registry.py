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
    # 应用资源恒 resource（doc35 §3）——「是 deck」由 resource_kind='deck.presentation' 与
    # source_app_id='deck' 表达，kind 只答「怎么打开」。
    assert descriptor.artifact_kind == 'resource'


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


# ── RC-P5 守卫①：descriptor↔doc08 域一致性（全应用，防漂移） ──────────────────────
# doc31 §8 守卫①：每个应用声明的资源描述符必须「域已注册 + open 语义一致」，且域全局唯一
# （两个应用不能声明同一 hasn:// 域）。RC-P6 逐应用铺开 resources[] 时，这组不变量自动兜住
# 「声明了域协议没登记 / route↔window 不一致 / 域撞车」漂移——是 §7 首号风险的拦截网。


def _all_declared_descriptors() -> list[tuple[str, dict]]:
    """收集全部 builtin 应用声明的原始 resource 条目（app_id, resource_dict）。"""
    out: list[tuple[str, dict]] = []
    for manifest in ai_native_app_registry.list_builtin_apps():
        app_id = manifest['app_id']
        resources = manifest.get('resources')
        if not isinstance(resources, list):
            continue
        out.extend((app_id, resource) for resource in resources)
    return out


def _descriptor_error(resource: dict) -> str | None:
    """校验单条 resource 描述符，合法返回 None、否则返回原因字符串。

    把 try/except 收进独立函数体（而非循环内），既规避 PERF203「循环内 try」性能告警，
    又复用于「硬失败」与「计数」两处守卫。
    """
    try:
        ResourceDescriptor.model_validate(resource)
    except Exception as exc:
        return str(exc)
    return None


def test_all_declared_descriptors_validate() -> None:
    """所有声明出来的 descriptor 必须能通过校验——声明了却越界=漂移，必须显式失败。

    （registry.resource_routes() 对越界条目静默跳过以不阻塞下发；本守卫在测试期把
    「声明但无效」暴露为硬失败，防止应用悄悄挂上打不开的资源。）
    """
    bad = [
        f'{app_id}: {resource.get("uri_domain", "?")} → {err}'
        for app_id, resource in _all_declared_descriptors()
        if (err := _descriptor_error(resource)) is not None
    ]
    assert not bad, '声明了却校验失败的资源描述符（漂移）:\n' + '\n'.join(bad)


def test_uri_domain_unique_across_apps() -> None:
    """uri_domain 全局唯一：两个应用不得声明同一 hasn:// 资源域（域撞车=解析错乱）。"""
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for app_id, resource in _all_declared_descriptors():
        domain = (resource.get('uri_domain') or '').strip()
        if not domain:
            continue
        if domain in seen and seen[domain] != app_id:
            collisions.append(f'{domain}: {seen[domain]} ↔ {app_id}')
        else:
            seen[domain] = app_id
    assert not collisions, 'uri_domain 撞车（同一域被多应用声明）:\n' + '\n'.join(collisions)


def test_resource_routes_no_silent_drop() -> None:
    """投影出的资源路由数 == 声明的有效 descriptor 数：无有效 descriptor 被静默丢弃。

    与 test_all_declared_descriptors_validate 合起来：声明的全有效 + 全被投影 = 零漂移。
    """
    declared_valid = sum(1 for _app_id, resource in _all_declared_descriptors() if _descriptor_error(resource) is None)
    assert len(ai_native_app_registry.resource_routes()) == declared_valid


def test_native_window_only_deck_and_design() -> None:
    """open 语义一致性：native_window 仅限 deck/design 两个独立窗口应用（V6 不变量）。

    避免新应用被 registry 误配成独立窗口（应走 internal_route/entry_query）。ResourceOpen
    的 schema 校验已挡住 window∉{deck,design}，这里再从「投影后的路由」侧断言一遍。
    """
    for route in ai_native_app_registry.resource_routes():
        if route.open_mode == 'native_window':
            assert route.window in ('deck', 'design'), (
                f'{route.app_id} 的 native_window 资源 window={route.window} 非法'
            )
        else:
            assert route.window is None, f'{route.app_id} 的非 native_window 资源不应带 window={route.window}'
