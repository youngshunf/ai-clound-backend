"""Presenton 演示文稿 AI-Native 接入 P1 云端半：manifest + WorkbenchApp + scope 词表。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/12-Presenton演示文稿应用接入设计.md §7/§8.2/§11.12。
真实校验（零 mock 被测代码）：注册表/校验器/scope catalog 全走真实实现，publish_builtin 用 db=None 纯路径。
"""

from __future__ import annotations

import pytest

# P1 声明的 5 个 scope（§11.12）；所有 capabilities 的 required_scopes 必须 ⊆ 此集合。
_PRESENTATION_SCOPES = {
    'presentation:read',
    'presentation:create',
    'presentation:generate',
    'presentation:manage',
    'image:generate',
}


def test_builtin_presentation_manifest_matches_contract() -> None:
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST

    m = PRESENTATION_AI_NATIVE_MANIFEST
    assert m['app_id'] == 'presentation'
    assert m['version'] == '1.0.0'
    assert m['workspace_scope'] == ['personal']
    assert m['collaboration_mode'] == 'none'
    assert m['execution_mode'] == 'embedded_desktop'
    assert m['transport_mode'] == 'hybrid'
    # 方案 A（§7.3 critical）：tools[] 置空，本地工具走 hasn-mcp source=Local，不进云端路由。
    assert m['tools'] == []
    assert m['publisher']['publisher_type'] == 'first_party'
    assert m['endpoints']['component_origin'] == 'loopback'
    # 11 个能力：10 presentation 代理 + hasn.image.generate（§8.2）。
    mcp_names = {c['mcp_name'] for c in m['capabilities']}
    assert mcp_names == {
        'hasn.presentation.generate',
        'hasn.presentation.generate_async',
        'hasn.presentation.get_status',
        'hasn.presentation.outline',
        'hasn.presentation.edit',
        'hasn.presentation.derive',
        'hasn.presentation.list',
        'hasn.presentation.get',
        'hasn.presentation.delete',
        'hasn.presentation.upload_file',
        'hasn.image.generate',
    }


def test_presentation_capabilities_scopes_within_declared_set() -> None:
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST

    for cap in PRESENTATION_AI_NATIVE_MANIFEST['capabilities']:
        for scope in cap['required_scopes']:
            assert scope in _PRESENTATION_SCOPES, f'{cap["mcp_name"]} 引用未声明 scope {scope}'
        # input_schema 字段必须完整（schema-on-error + 引导 function-calling LLM 填参，§8.3）。
        assert cap['input_schema']['type'] == 'object'
        assert 'properties' in cap['input_schema']


def test_presentation_scope_mapping_per_design() -> None:
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST

    scope_of = {c['mcp_name']: c['required_scopes'][0] for c in PRESENTATION_AI_NATIVE_MANIFEST['capabilities']}
    assert scope_of['hasn.presentation.list'] == 'presentation:read'
    assert scope_of['hasn.presentation.get'] == 'presentation:read'
    assert scope_of['hasn.presentation.get_status'] == 'presentation:read'
    assert scope_of['hasn.presentation.outline'] == 'presentation:create'
    assert scope_of['hasn.presentation.generate'] == 'presentation:generate'
    assert scope_of['hasn.presentation.edit'] == 'presentation:generate'
    assert scope_of['hasn.presentation.derive'] == 'presentation:generate'
    assert scope_of['hasn.presentation.delete'] == 'presentation:manage'
    assert scope_of['hasn.presentation.upload_file'] == 'presentation:manage'
    assert scope_of['hasn.image.generate'] == 'image:generate'


def test_manifest_validator_accepts_builtin_presentation_manifest() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    registry = AINativeAppRegistry(workbench_registry=workbench_app_registry)
    result = registry.validate_manifest(PRESENTATION_AI_NATIVE_MANIFEST)

    assert result.valid is True, result.errors
    assert result.errors == []
    assert result.manifest_hash.startswith('sha256:')


def test_presentation_workbench_app_registered_with_embedded_fields() -> None:
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    app = workbench_app_registry.get('presentation')
    assert app.execution_mode == 'embedded_desktop'
    assert app.ui_kind == 'embedded_webview'
    assert app.window_url == '/api/v1/apps/presentation/ui/upload'
    assert app.window_origin == 'loopback'
    assert app.scope == ('personal',)
    assert app.collaboration_mode == 'none'
    assert app.install_policy == 'auto'

    manifest = app.to_manifest()
    # 三处端到端同步（§6.1）：新字段须随 to_manifest 透传给 daemon/WebUI。
    assert manifest['execution_mode'] == 'embedded_desktop'
    assert manifest['ui_kind'] == 'embedded_webview'
    assert manifest['window_url'] == '/api/v1/apps/presentation/ui/upload'
    assert manifest['window_origin'] == 'loopback'


def test_existing_workbench_apps_keep_cloud_defaults() -> None:
    # 向后兼容：未声明 embedded 字段的既有 app（knowledge/community）应得 cloud 默认 + None。
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    knowledge = workbench_app_registry.get('knowledge')
    assert knowledge.execution_mode == 'cloud'
    assert knowledge.ui_kind is None
    assert knowledge.to_manifest()['ui_kind'] is None


def test_presentation_in_builtin_registry() -> None:
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    builtin_ids = {m['app_id'] for m in ai_native_app_registry.list_builtin_apps()}
    assert 'presentation' in builtin_ids
    manifest = ai_native_app_registry.get_builtin_manifest('presentation')
    assert manifest['app_id'] == 'presentation'


def test_presentation_scopes_present_in_catalog() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG, scope_meta

    for scope in _PRESENTATION_SCOPES:
        assert scope in SCOPE_CATALOG, f'{scope} 未在 SCOPE_CATALOG 声明'
        meta = scope_meta(scope)
        # 非回退（回退时 label == key），即真有中文展示元数据。
        assert meta['label'] != scope
        assert meta['domain'] in ('presentation', 'image')
    # 风险等级对齐 §11.12。
    assert SCOPE_CATALOG['presentation:read']['risk'] == 'low'
    assert SCOPE_CATALOG['presentation:generate']['risk'] == 'high'
    assert SCOPE_CATALOG['presentation:manage']['risk'] == 'high'


@pytest.mark.asyncio
async def test_publish_builtin_presentation_uses_published_status() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    registry = AINativeAppRegistry(workbench_registry=workbench_app_registry)
    saved = await registry.publish_builtin(None, 'presentation')

    assert saved['app_id'] == 'presentation'
    assert saved['status'] == 'published'
    assert saved['manifest_json'] == PRESENTATION_AI_NATIVE_MANIFEST
    assert saved['manifest_hash'].startswith('sha256:')


def test_presentation_emit_declaration_present() -> None:
    from backend.app.hasn.service.ai_native_builtin_presentation import PRESENTATION_AI_NATIVE_MANIFEST

    emit = PRESENTATION_AI_NATIVE_MANIFEST['notifications']['emit']
    assert emit['categories'] == ['app']
    assert emit['card_message'] is True
    assert emit['display_name'] == '演示文稿'
