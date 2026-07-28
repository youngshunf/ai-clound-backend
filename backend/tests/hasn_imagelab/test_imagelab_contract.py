"""图坊云端 manifest 与 IMG4 公共契约的纯 Python 对拍测试。"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_imagelab.manifest import IMAGELAB_AI_NATIVE_MANIFEST


EXPECTED_TOOL_SCOPES = {
    'workspace.get': 'imagelab:read',
    'analyze': 'imagelab:read',
    'process': 'imagelab:process',
    'pipeline': 'imagelab:process',
    'batch': 'imagelab:batch',
    'job.get': 'imagelab:read',
    'job.list': 'imagelab:read',
    'animate': 'imagelab:process',
    'retouch': 'imagelab:destructive',
    'enhance': 'imagelab:process',
    'generate': 'imagelab:generate',
    'recipe.save': 'imagelab:process',
    'recipe.list': 'imagelab:process',
    'recipe.get': 'imagelab:process',
    'export': 'imagelab:export',
    'share': 'imagelab:share',
    'import': 'imagelab:process',
}


def _property_names(value: Any) -> set[str]:
    """递归收集 JSON Schema 中的属性名。"""
    if isinstance(value, dict):
        names = set(value.get('properties', {}))
        for child in value.values():
            names.update(_property_names(child))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for child in value:
            names.update(_property_names(child))
        return names
    return set()


def test_imagelab_manifest_matches_seventeen_tool_contract() -> None:
    capabilities = IMAGELAB_AI_NATIVE_MANIFEST['capabilities']
    by_name = {capability['tool_id'].split('.', 1)[1]: capability for capability in capabilities}

    assert set(by_name) == set(EXPECTED_TOOL_SCOPES)
    assert len(capabilities) == 17
    for name, scope in EXPECTED_TOOL_SCOPES.items():
        capability = by_name[name]
        assert capability['mcp_name'] == f'hasn.imagelab.{name}'
        assert capability['required_scopes'] == [scope]


def test_only_share_uses_factory_ask_mode() -> None:
    capabilities = IMAGELAB_AI_NATIVE_MANIFEST['capabilities']
    asks = {
        capability['mcp_name']
        for capability in capabilities
        if capability['human_confirmation']['required'] is True
    }
    assert asks == {'hasn.imagelab.share'}


def test_manifest_uses_stable_project_and_input_contract() -> None:
    manifest = IMAGELAB_AI_NATIVE_MANIFEST
    assert manifest['project_aware'] is True
    assert manifest['project_required'] is True
    assert manifest['project_integration'] == 'project_required'
    assert manifest['resources'][0]['uri_domain'] == 'imagelab/projects'

    capabilities = manifest['capabilities']
    schemas = {capability['mcp_name']: capability['input_schema'] for capability in capabilities}
    workspace_schema = schemas['hasn.imagelab.workspace.get']
    assert workspace_schema['required'] == ['platform_project_id']
    assert set(workspace_schema['properties']) == {'platform_project_id'}

    for tool_name, schema in schemas.items():
        property_names = _property_names(schema)
        assert 'path' not in property_names, tool_name
        assert 'output_dir' not in property_names, tool_name
        assert not any(name.endswith('_base64') for name in property_names), tool_name
