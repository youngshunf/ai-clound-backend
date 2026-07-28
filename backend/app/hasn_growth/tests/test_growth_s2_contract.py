"""获客项目化 S2 manifest、挂靠与资源 ACL 静态契约测试。"""

from fastapi.routing import APIRoute

from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.app_catalog_registry import app_catalog_registry
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn_growth.api.v1.app.growth import router as growth_app_router
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry


def test_growth_manifest_requires_project_and_declares_four_unique_resources() -> None:
    assert GROWTH_AI_NATIVE_MANIFEST['project_aware'] is True
    assert GROWTH_AI_NATIVE_MANIFEST['project_required'] is True
    assert GROWTH_AI_NATIVE_MANIFEST['project_integration'] == 'project_required'

    app = app_catalog_registry.get('growth')
    assert app.project_aware is True
    assert app.project_required is True

    resources = GROWTH_AI_NATIVE_MANIFEST['resources']
    assert {resource['resource_kind'] for resource in resources} == {
        'growth.project',
        'growth.leads',
        'growth.customer',
        'growth.opportunity',
    }
    assert {resource['ref_type'] for resource in resources} == {
        'project',
        'leads',
        'customer',
        'opportunity',
    }
    assert len({resource['uri_domain'] for resource in resources}) == 4
    assert all(resource['artifact_kind'] == 'resource' for resource in resources)

    validation = ai_native_app_registry.validate_manifest(GROWTH_AI_NATIVE_MANIFEST)
    assert validation.valid, validation.errors


def test_growth_resource_descriptor_resolution_never_falls_back() -> None:
    descriptor = ai_native_app_registry.resource_descriptor('growth', 'growth.customer')
    assert descriptor is not None
    assert descriptor.uri_domain == 'growth/customers'
    assert descriptor.build_uri(42) == 'hasn://growth/customers/42'

    assert ai_native_app_registry.resource_descriptor('growth', 'growth.unknown') is None
    unknown, server_id = ai_native_app_registry.resolve_resource_descriptor(
        'growth',
        'unknown:42',
    )
    assert unknown is None
    assert server_id is None


def test_growth_linkage_adapter_uses_real_columns_and_forbids_detach() -> None:
    artifact_adapter = project_linkage_registry.get('artifact')
    assert artifact_adapter is not None
    assert artifact_adapter.allow_unlink is True
    assert artifact_adapter.allow_relink is True

    adapter = project_linkage_registry.get('growth/projects')
    assert adapter is not None
    assert adapter.id_column == 'id'
    assert adapter.owner_column == 'owner_hasn_id'
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.id_is_uuid is True
    assert adapter.is_container is True
    assert adapter.app_id == 'growth'
    assert adapter.kind == 'growth_project'
    assert adapter.allow_unlink is False
    assert adapter.allow_relink is False
    assert adapter.related_resource_uris is not None
    assert adapter.related_resource_uri_pairs is not None


def test_growth_resource_acl_adapters_are_registered() -> None:
    registered = resource_kind_registry.registered_types()
    assert {
        'growth_project',
        'growth_leads',
        'growth_customer',
        'growth_opportunity',
    } <= registered


def test_growth_owner_project_routes_exist_without_rebind() -> None:
    paths = {
        route.path
        for route in growth_app_router.routes
        if isinstance(route, APIRoute)
    }
    assert '/projects/by-platform/{platform_project_id}' in paths
    assert '/projects/{growth_project_id}' in paths
    assert '/projects' in paths
    assert not [path for path in paths if 'rebind' in path]
