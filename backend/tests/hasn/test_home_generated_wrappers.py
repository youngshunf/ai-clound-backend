from __future__ import annotations

import importlib
import inspect

from types import SimpleNamespace
from typing import Any

import pytest

CRUD_CASES = (
    (
        'backend.app.hasn.crud.crud_hasn_agent_capabilities',
        'CRUDHasnAgentCapabilities',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_agent_runtime_reports',
        'CRUDHasnAgentRuntimeReports',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_agents',
        'CRUDHasnAgents',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_audit_log',
        'CRUDHasnAuditLog',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_channel_bindings',
        'CRUDHasnChannelBindings',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_clients',
        'CRUDHasnClients',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_contacts',
        'CRUDHasnContacts',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_conversations',
        'CRUDHasnConversations',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_enterprise',
        'CRUDHasnEnterprise',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_enterprise_invite_code',
        'CRUDHasnEnterpriseInviteCode',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_enterprise_membership',
        'CRUDHasnEnterpriseMembership',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_group_members',
        'CRUDHasnGroupMembers',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_humans',
        'CRUDHasnHumans',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_messages',
        'CRUDHasnMessages',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_node_bindings',
        'CRUDHasnNodeBindings',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_nodes',
        'CRUDHasnNodes',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_notifications',
        'CRUDHasnNotifications',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_owner_api_keys',
        'CRUDHasnOwnerApiKeys',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_pending_intents',
        'CRUDHasnPendingIntents',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_app_credential',
        'CRUDHasnAppCredential',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_app_instance',
        'CRUDHasnAppInstance',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_suppressed_messages',
        'CRUDHasnSuppressedMessages',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_sync_events',
        'CRUDHasnSyncEvents',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_sync_inbox_events',
        'CRUDHasnSyncInboxEvents',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_trade_sessions',
        'CRUDHasnTradeSessions',
        'id__in',
    ),
    (
        'backend.app.hasn.crud.crud_hasn_unread_counts',
        'CRUDHasnUnreadCounts',
        'id__in',
    ),
    # 应用平台 v3 P3（设计 17 决策①②）：crud_hasn_user_active_workspace /
    # crud_hasn_workspace_app 随两表退役一并删除。
)

SERVICE_CASES = (
    (
        'backend.app.hasn.service.hasn_agent_capabilities_service',
        'HasnAgentCapabilitiesService',
        'hasn_agent_capabilities_dao',
    ),
    (
        'backend.app.hasn.service.hasn_agent_runtime_reports_service',
        'HasnAgentRuntimeReportsService',
        'hasn_agent_runtime_reports_dao',
    ),
    (
        'backend.app.hasn.service.hasn_audit_log_service',
        'HasnAuditLogService',
        'hasn_audit_log_dao',
    ),
    (
        'backend.app.hasn.service.hasn_channel_bindings_service',
        'HasnChannelBindingsService',
        'hasn_channel_bindings_dao',
    ),
    (
        'backend.app.hasn.service.hasn_clients_service',
        'HasnClientsService',
        'hasn_clients_dao',
    ),
    (
        'backend.app.hasn.service.hasn_conversations_service',
        'HasnConversationsService',
        'hasn_conversations_dao',
    ),
    (
        'backend.app.hasn.service.hasn_enterprise_service',
        'HasnEnterpriseService',
        'hasn_enterprise_dao',
    ),
    (
        'backend.app.hasn.service.hasn_enterprise_invite_code_service',
        'HasnEnterpriseInviteCodeService',
        'hasn_enterprise_invite_code_dao',
    ),
    (
        'backend.app.hasn.service.hasn_enterprise_membership_service',
        'HasnEnterpriseMembershipService',
        'hasn_enterprise_membership_dao',
    ),
    (
        'backend.app.hasn.service.hasn_group_members_service',
        'HasnGroupMembersService',
        'hasn_group_members_dao',
    ),
    (
        'backend.app.hasn.service.hasn_humans_service',
        'HasnHumansService',
        'hasn_humans_dao',
    ),
    (
        'backend.app.hasn.service.hasn_messages_service',
        'HasnMessagesService',
        'hasn_messages_dao',
    ),
    (
        'backend.app.hasn.service.hasn_node_bindings_service',
        'HasnNodeBindingsService',
        'hasn_node_bindings_dao',
    ),
    (
        'backend.app.hasn.service.hasn_notifications_service',
        'HasnNotificationsService',
        'hasn_notifications_dao',
    ),
    (
        'backend.app.hasn.service.hasn_pending_intents_service',
        'HasnPendingIntentsService',
        'hasn_pending_intents_dao',
    ),
    # 实施 03 收编：hasn_ragflow_{instance,credential}_service 已删除（知识库实例/凭据
    # 并入 hasn_app_instance/hasn_app_credential，控制面走 instance_resolver +
    # workbench_domain_service，无 per-table generated service）。
    (
        'backend.app.hasn.service.hasn_suppressed_messages_service',
        'HasnSuppressedMessagesService',
        'hasn_suppressed_messages_dao',
    ),
    (
        'backend.app.hasn.service.hasn_sync_events_service',
        'HasnSyncEventsService',
        'hasn_sync_events_dao',
    ),
    (
        'backend.app.hasn.service.hasn_sync_inbox_events_service',
        'HasnSyncInboxEventsService',
        'hasn_sync_inbox_events_dao',
    ),
    (
        'backend.app.hasn.service.hasn_trade_sessions_service',
        'HasnTradeSessionsService',
        'hasn_trade_sessions_dao',
    ),
    (
        'backend.app.hasn.service.hasn_unread_counts_service',
        'HasnUnreadCountsService',
        'hasn_unread_counts_dao',
    ),
    # 应用平台 v3 P3（设计 17 决策①②）：hasn_user_active_workspace_service /
    # hasn_workspace_app_service 随两表退役一并删除。
)

GENERATED_API_MODULES = (
    'backend.app.hasn.api.v1.admin.hasn_agent_capabilities',
    'backend.app.hasn.api.v1.admin.hasn_agent_runtime_reports',
    'backend.app.hasn.api.v1.admin.hasn_audit_log',
    'backend.app.hasn.api.v1.admin.hasn_channel_bindings',
    'backend.app.hasn.api.v1.admin.hasn_clients',
    'backend.app.hasn.api.v1.admin.hasn_contacts',
    'backend.app.hasn.api.v1.admin.hasn_conversations',
    'backend.app.hasn.api.v1.admin.hasn_group_members',
    'backend.app.hasn.api.v1.admin.hasn_humans',
    'backend.app.hasn.api.v1.admin.hasn_messages',
    'backend.app.hasn.api.v1.admin.hasn_node_bindings',
    'backend.app.hasn.api.v1.admin.hasn_nodes',
    'backend.app.hasn.api.v1.admin.hasn_notifications',
    'backend.app.hasn.api.v1.admin.hasn_owner_api_keys',
    'backend.app.hasn.api.v1.admin.hasn_pending_intents',
    'backend.app.hasn.api.v1.admin.hasn_suppressed_messages',
    'backend.app.hasn.api.v1.admin.hasn_sync_events',
    'backend.app.hasn.api.v1.admin.hasn_sync_inbox_events',
    'backend.app.hasn.api.v1.admin.hasn_trade_sessions',
    'backend.app.hasn.api.v1.admin.hasn_unread_counts',
    'backend.app.hasn.api.v1.open.hasn_agent_capabilities',
    'backend.app.hasn.api.v1.open.hasn_agents',
    'backend.app.hasn.api.v1.open.hasn_audit_log',
    'backend.app.hasn.api.v1.open.hasn_contacts',
    'backend.app.hasn.api.v1.open.hasn_conversations',
    'backend.app.hasn.api.v1.open.hasn_group_members',
    'backend.app.hasn.api.v1.open.hasn_humans',
    'backend.app.hasn.api.v1.open.hasn_messages',
    'backend.app.hasn.api.v1.open.hasn_notifications',
    'backend.app.hasn.api.v1.open.hasn_trade_sessions',
    'backend.app.hasn.api.v1.open.hasn_unread_counts',
    'backend.app.hasn.api.v1.agent.hasn_nodes',
    'backend.app.hasn.api.v1.agent.hasn_owner_api_keys',
    'backend.app.hasn.api.v1.app.hasn_humans',
    'backend.app.hasn.api.v1.app.hasn_nodes',
    'backend.app.hasn.api.v1.app.hasn_owner_api_keys',
)


class CapturingCRUD:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def select_model(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append(('select_model', args, kwargs))
        return {'selected': args[1]}

    async def select_order(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append(('select_order', args, kwargs))
        return {'order': args}

    async def select_models(self, *args: Any, **kwargs: Any) -> list[object]:
        self.calls.append(('select_models', args, kwargs))
        return [{'all': True}]

    async def create_model(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(('create_model', args, kwargs))

    async def update_model(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(('update_model', args, kwargs))
        return 3

    async def delete_model_by_column(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(('delete_model_by_column', args, kwargs))
        return 4


class CapturingDAO:
    def __init__(self, item: object | None = None) -> None:
        self.item = item
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def get(self, db: object, pk: int) -> object | None:
        self.calls.append(('get', (db, pk)))
        return self.item

    async def get_select(self) -> object:
        self.calls.append(('get_select', ()))
        return {'select': True}

    async def get_all(self, db: object) -> list[object]:
        self.calls.append(('get_all', (db,)))
        return ['all-items']

    async def create(self, db: object, obj: object) -> None:
        self.calls.append(('create', (db, obj)))

    async def update(self, db: object, pk: int, obj: object) -> int:
        self.calls.append(('update', (db, pk, obj)))
        return 5

    async def delete(self, db: object, pks: list[int]) -> int:
        self.calls.append(('delete', (db, pks)))
        return 6


class CapturingGeneratedService:
    def __init__(self, *, count: int = 1, owner_user_id: int = 101) -> None:
        self.count = count
        self.owner_user_id = owner_user_id
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def get(self, **kwargs: object) -> object:
        self.calls.append(('get', kwargs))
        return SimpleNamespace(id=kwargs.get('pk', 1), user_id=self.owner_user_id)

    async def get_list(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(('get_list', kwargs))
        return {'items': [{'id': 1}]}

    async def create(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(('create', kwargs))
        return {'id': 2}

    async def update(self, **kwargs: object) -> int:
        self.calls.append(('update', kwargs))
        return self.count

    async def delete(self, **kwargs: object) -> int:
        self.calls.append(('delete', kwargs))
        return self.count


def _module_service_names(module: object) -> list[str]:
    names = []
    for name, value in vars(module).items():
        if name.endswith('_service') and all(
            hasattr(value, attr) for attr in ('get', 'get_list', 'create', 'update', 'delete')
        ):
            names.append(name)
    return names


def _endpoint_kwargs(endpoint: Any, *, owner_user_id: int = 101) -> dict[str, object]:
    request = SimpleNamespace(
        user=SimpleNamespace(id=owner_user_id),
        state=SimpleNamespace(agent=SimpleNamespace(owner_user_id=owner_user_id)),
    )
    obj = SimpleNamespace(pks=[1])
    values: dict[str, object] = {}
    for name in inspect.signature(endpoint).parameters:
        if name == 'request':
            values[name] = request
        elif name == 'db':
            values[name] = object()
        elif name == 'pk':
            values[name] = 1
        elif name == 'obj':
            values[name] = obj
    return values


async def _call_standard_endpoint(endpoint: Any, *, owner_user_id: int = 101) -> object:
    return await endpoint(**_endpoint_kwargs(endpoint, owner_user_id=owner_user_id))


@pytest.mark.parametrize(('module_name', 'class_name', 'delete_key'), CRUD_CASES)
@pytest.mark.asyncio
async def test_generated_crud_wrappers_delegate_to_crud_plus(
    module_name: str, class_name: str, delete_key: str
) -> None:
    module = pytest.importorskip(module_name)
    crud = getattr(module, class_name).__new__(getattr(module, class_name))
    capture = CapturingCRUD()

    for name in (
        'select_model',
        'select_order',
        'select_models',
        'create_model',
        'update_model',
        'delete_model_by_column',
    ):
        setattr(crud, name, getattr(capture, name))

    db = object()
    obj = object()

    assert await crud.get(db, 11) == {'selected': 11}
    assert await crud.get_select() == {'order': ('id' if delete_key == 'id__in' else 'user_id', 'desc')}
    assert await crud.get_all(db) == [{'all': True}]
    assert await crud.create(db, obj) is None
    assert await crud.update(db, 12, obj) == 3
    assert await crud.delete(db, [13, 14]) == 4

    assert capture.calls == [
        ('select_model', (db, 11), {}),
        ('select_order', ('id' if delete_key == 'id__in' else 'user_id', 'desc'), {}),
        ('select_models', (db,), {}),
        ('create_model', (db, obj), {}),
        ('update_model', (db, 12, obj), {}),
        ('delete_model_by_column', (db,), {'allow_multiple': True, delete_key: [13, 14]}),
    ]


@pytest.mark.parametrize(('module_name', 'class_name', 'dao_name'), SERVICE_CASES)
@pytest.mark.asyncio
async def test_generated_services_delegate_to_dao_and_raise_not_found(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    class_name: str,
    dao_name: str,
) -> None:
    module = pytest.importorskip(module_name)
    service = getattr(module, class_name)
    item = SimpleNamespace(id=21)
    dao = CapturingDAO(item=item)
    db = object()
    obj = SimpleNamespace(pks=[31, 32])
    paged: list[object] = []

    async def fake_paging_data(received_db: object, select: object) -> dict[str, object]:
        paged.append((received_db, select))
        return {'items': ['paged']}

    monkeypatch.setattr(module, dao_name, dao)
    monkeypatch.setattr(module, 'paging_data', fake_paging_data)

    assert await service.get(db=db, pk=21) is item
    assert await service.get_list(db) == {'items': ['paged']}
    assert await service.get_all(db=db) == ['all-items']
    assert await service.create(db=db, obj=obj) is None
    assert await service.update(db=db, pk=22, obj=obj) == 5
    assert await service.delete(db=db, obj=obj) == 6
    assert paged == [(db, {'select': True})]
    assert dao.calls == [
        ('get', (db, 21)),
        ('get_select', ()),
        ('get_all', (db,)),
        ('create', (db, obj)),
        ('update', (db, 22, obj)),
        ('delete', (db, [31, 32])),
    ]

    monkeypatch.setattr(module, dao_name, CapturingDAO(item=None))
    with pytest.raises(Exception) as exc_info:
        await service.get(db=db, pk=404)
    assert exc_info.value.__class__.__name__ == 'NotFoundError'


@pytest.mark.parametrize('module_name', GENERATED_API_MODULES)
@pytest.mark.asyncio
async def test_generated_api_endpoints_delegate_to_services(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)
    service_names = _module_service_names(module)
    assert len(service_names) == 1

    service = CapturingGeneratedService()
    monkeypatch.setattr(module, service_names[0], service)

    endpoints = [
        value
        for name, value in vars(module).items()
        if inspect.iscoroutinefunction(value)
        and not name.startswith('toggle_')
        and name != 'agent_report_agent_heartbeat'
        and {'db'} <= set(inspect.signature(value).parameters)
    ]
    assert endpoints

    for endpoint in endpoints:
        await _call_standard_endpoint(endpoint)

    methods = [method for method, _ in service.calls]
    assert methods
    assert all(method in {'get', 'get_list', 'create', 'update', 'delete'} for method in methods)


@pytest.mark.parametrize(
    'module_name',
    (
        'backend.app.hasn.api.v1.admin.hasn_agent_capabilities',
        'backend.app.hasn.api.v1.agent.hasn_nodes',
        'backend.app.hasn.api.v1.app.hasn_humans',
    ),
)
@pytest.mark.asyncio
async def test_generated_api_update_and_delete_fail_when_service_changes_no_rows(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)
    service_names = _module_service_names(module)
    assert len(service_names) == 1

    service = CapturingGeneratedService(count=0)
    monkeypatch.setattr(module, service_names[0], service)

    endpoints = [
        value
        for name, value in vars(module).items()
        if inspect.iscoroutinefunction(value)
        and (
            name.startswith(('update', 'delete', 'agent_update', 'agent_delete'))
        )
    ]
    assert endpoints

    for endpoint in endpoints:
        await _call_standard_endpoint(endpoint)

    assert {method for method, _ in service.calls} <= {'get', 'update', 'delete'}


@pytest.mark.parametrize(
    'module_name',
    (
        'backend.app.hasn.api.v1.agent.hasn_nodes',
        'backend.app.hasn.api.v1.app.hasn_humans',
        'backend.app.hasn.api.v1.app.hasn_nodes',
        'backend.app.hasn.api.v1.app.hasn_owner_api_keys',
    ),
)
@pytest.mark.asyncio
async def test_generated_api_owner_mismatch_raises_forbidden(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)
    service_names = _module_service_names(module)
    assert len(service_names) == 1

    service = CapturingGeneratedService(owner_user_id=202)
    monkeypatch.setattr(module, service_names[0], service)

    guarded = [
        value
        for name, value in vars(module).items()
        if inspect.iscoroutinefunction(value)
        and (
            name.startswith(('get_', 'update_', 'delete_', 'agent_get', 'agent_update', 'agent_delete'))
        )
        and 'pk' in inspect.signature(value).parameters
    ]
    assert guarded

    for endpoint in guarded:
        with pytest.raises(Exception) as exc_info:
            await _call_standard_endpoint(endpoint, owner_user_id=101)
        assert exc_info.value.__class__.__name__ == 'ForbiddenError'
