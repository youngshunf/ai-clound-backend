from copy import deepcopy

from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry


def test_builtin_manifests_declare_consistent_project_integration_tier() -> None:
    """所有一方应用都必须声明可验证的平台项目接入档位。"""
    expected_flags = {
        'artifact_only': (False, False),
        'project_aware': (True, False),
        'project_required': (True, True),
    }

    manifests = ai_native_app_registry.list_builtin_apps()
    manifest_ids = {manifest['app_id'] for manifest in manifests}
    catalog_ids = {app.id for app in ai_native_app_registry.catalog_registry.list()}

    assert manifest_ids == catalog_ids

    for manifest in manifests:
        tier = manifest.get('project_integration')

        assert tier in expected_flags, manifest['app_id']
        assert (manifest.get('project_aware'), manifest.get('project_required')) == expected_flags[tier]
        assert ai_native_app_registry.validate_manifest(manifest).valid, manifest['app_id']


def test_manifest_rejects_missing_or_unknown_project_integration_tier() -> None:
    manifest = deepcopy(ai_native_app_registry.get_builtin_manifest('project'))
    manifest.pop('project_integration')

    missing = ai_native_app_registry.validate_manifest(manifest)

    assert missing.valid is False
    assert 'project_integration_required' in missing.errors

    manifest['project_integration'] = 'unknown'
    unknown = ai_native_app_registry.validate_manifest(manifest)

    assert unknown.valid is False
    assert 'project_integration_invalid' in unknown.errors
