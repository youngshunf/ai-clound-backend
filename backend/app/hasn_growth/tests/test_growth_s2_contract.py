"""获客项目化 S2 manifest、挂靠与资源 ACL 静态契约测试。"""

from types import SimpleNamespace
from uuid import UUID

from fastapi.routing import APIRoute

from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.app_catalog_registry import app_catalog_registry
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.app.hasn_growth.api.v1.app.growth import router as growth_app_router
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_growth.service.funnel_service import _customer_to_dict
from backend.app.hasn_growth.service.opportunity_flow_service import (
    _opportunity_to_dict,
)
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
    assert adapter.validate_link is not None
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
    assert '/opportunities/{opportunity_id}' in paths
    assert not [path for path in paths if 'rebind' in path]


def test_growth_resource_serializers_expose_authoritative_project_id() -> None:
    growth_project_id = UUID('00000000-0000-4000-8000-000000000901')
    customer = SimpleNamespace(
        id=1,
        growth_project_id=growth_project_id,
        customer_no='C1',
        lead_contact_id=None,
        source_kind='manual',
        company_name='测试公司',
        contact_name=None,
        email=None,
        phone=None,
        wechat=None,
        im_refs=None,
        profile_json=None,
        intent_score=0,
        lifecycle_status='lead',
        owner_agent_id=None,
        owner_scope='personal',
        enterprise_id=None,
        assignee=None,
        followup_task_id=None,
        tags=None,
        last_activity_at=None,
        next_followup_at=None,
        silent_round_count=0,
        created_time=None,
    )
    opportunity = SimpleNamespace(
        id=2,
        growth_project_id=growth_project_id,
        opportunity_no='O2',
        customer_id=1,
        name='测试商机',
        stage='contacted',
        amount=None,
        currency='CNY',
        probability=None,
        expected_close_at=None,
        won_at=None,
        lost_at=None,
        lost_reason=None,
        close_note=None,
        created_by_kind='owner',
        owner_scope='personal',
        enterprise_id=None,
        assignee=None,
        created_time=None,
    )

    assert _customer_to_dict(customer)['growth_project_id'] == str(
        growth_project_id
    )
    assert _opportunity_to_dict(opportunity)['growth_project_id'] == str(
        growth_project_id
    )
