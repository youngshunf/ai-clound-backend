from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED_TABLES = (
    'hasn_enterprise',
    'hasn_enterprise_membership',
    'hasn_enterprise_invite_code',
    'hasn_user_active_workspace',
    'hasn_workspace_app',
    'hasn_ragflow_instance',
    'hasn_ragflow_credential',
)


def test_workbench_plan_sql_and_codegen_foundation_exist() -> None:
    missing: list[str] = []
    for table in REQUIRED_TABLES:
        expected = {
            REPO_ROOT / 'backend' / 'sql' / 'hasn' / f'{table}.sql',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / f'crud_{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / f'{table}_service.py',
        }
        missing.extend(path.relative_to(REPO_ROOT).as_posix() for path in expected if not path.exists())

    assert missing == []


def test_workbench_codegen_schemas_validate_workspace_and_instance_invariants() -> None:
    from pydantic import ValidationError

    from backend.app.hasn.schema.hasn_enterprise import CreateHasnEnterpriseParam
    from backend.app.hasn.schema.hasn_enterprise_invite_code import CreateHasnEnterpriseInviteCodeParam
    from backend.app.hasn.schema.hasn_enterprise_membership import CreateHasnEnterpriseMembershipParam
    from backend.app.hasn.schema.hasn_ragflow_credential import CreateHasnRagflowCredentialParam
    from backend.app.hasn.schema.hasn_ragflow_instance import CreateHasnRagflowInstanceParam
    from backend.app.hasn.schema.hasn_user_active_workspace import CreateHasnUserActiveWorkspaceParam
    from backend.app.hasn.schema.hasn_workspace_app import CreateHasnWorkspaceAppParam

    enterprise = CreateHasnEnterpriseParam(
        name='Acme',
        slug='acme',
        owner_user_id=7,
    )
    membership = CreateHasnEnterpriseMembershipParam(
        enterprise_id=1,
        user_id=8,
    )
    invite = CreateHasnEnterpriseInviteCodeParam(
        enterprise_id=1,
        code='JOIN-ACME',
        created_by=7,
    )
    workspace = CreateHasnUserActiveWorkspaceParam(
        user_id=8,
        kind='enterprise',
        enterprise_id=1,
    )
    workspace_app = CreateHasnWorkspaceAppParam(
        workspace_kind='personal',
        user_id=8,
        app_id='knowledge',
    )
    instance = CreateHasnRagflowInstanceParam(
        scope='enterprise',
        enterprise_id=1,
        url='https://knowledge.example',
        admin_api_key_encrypted=b'encrypted',
        public_pem='pem',
    )
    credential = CreateHasnRagflowCredentialParam(
        user_id=8,
        instance_id=1,
        ragflow_user_id='rf-user',
        ragflow_tenant_id='rf-tenant',
        api_key_encrypted=b'secret',
    )

    assert enterprise.status == 'active'
    assert membership.status == 'pending'
    assert invite.used_count == 0
    assert workspace.enterprise_id == 1
    assert workspace_app.status == 'active'
    assert instance.status == 'pending_config'
    assert credential.status == 'pending'

    with pytest.raises(ValidationError):
        CreateHasnUserActiveWorkspaceParam(user_id=8, kind='personal', enterprise_id=1)
    with pytest.raises(ValidationError):
        CreateHasnWorkspaceAppParam(workspace_kind='enterprise', user_id=8, app_id='knowledge')
    with pytest.raises(ValidationError):
        CreateHasnRagflowInstanceParam(
            scope='public',
            enterprise_id=1,
            url='https://knowledge.example',
            admin_api_key_encrypted=b'encrypted',
            public_pem='pem',
        )


def test_workbench_codegen_admin_api_modules_import_and_mount() -> None:
    from backend.app.hasn.api.router import v1

    routes = {route.path for route in v1.routes}

    assert '/api/v1/hasn/enterprises' in routes
    assert '/api/v1/hasn/enterprise/memberships' in routes
    assert '/api/v1/hasn/enterprise/invite-codes' in routes
    assert '/api/v1/hasn/user/active-workspaces' in routes
    assert '/api/v1/hasn/workspace/apps' in routes
    assert '/api/v1/hasn/ragflow/instances' in routes
    assert '/api/v1/hasn/ragflow/credentials' in routes


@pytest.mark.asyncio
async def test_enterprise_event_bus_dispatches_subscribers_in_order() -> None:
    from backend.app.hasn.service.enterprise_event_bus import EnterpriseEventBus

    bus = EnterpriseEventBus()
    events: list[tuple[str, dict]] = []

    def first(payload: dict) -> None:
        events.append(('first', payload))

    def second(payload: dict) -> None:
        events.append(('second', payload))

    bus.subscribe('on_workspace_switched', first)
    bus.subscribe('on_workspace_switched', second)

    await bus.publish('on_workspace_switched', {'user_id': 7})

    assert events == [
        ('first', {'user_id': 7}),
        ('second', {'user_id': 7}),
    ]


def test_invite_code_state_machine_rejects_invalid_codes() -> None:
    from backend.app.hasn.service.enterprise_application_service import InviteCodePolicy

    active = InviteCodePolicy(max_uses=2, used_count=1, revoked=False, expires_at=None)
    assert active.validate() is None

    used_up = InviteCodePolicy(max_uses=2, used_count=2, revoked=False, expires_at=None)
    assert used_up.validate() == 'invite_code_used_up'

    revoked = InviteCodePolicy(max_uses=None, used_count=0, revoked=True, expires_at=None)
    assert revoked.validate() == 'invite_code_revoked'


def test_workbench_registry_auto_installs_knowledge_for_personal_and_enterprise() -> None:
    from backend.app.hasn.service.workbench_app_registry import WorkbenchAppRegistry

    registry = WorkbenchAppRegistry.default()

    # community 已作为内置应用纳入自动安装（与 knowledge 同为 install_policy=auto）；
    # presentation（演示文稿 embedded_desktop，设计 §7.2）scope=personal + install_policy=auto，
    # 故 personal 工作空间额外自动安装，enterprise 不含（presentation 仅 personal scope）。
    assert [app.id for app in registry.auto_install_apps('personal')] == ['knowledge', 'community', 'presentation']
    assert [app.id for app in registry.auto_install_apps('enterprise')] == ['knowledge', 'community']
    assert registry.get('knowledge').entry_route == '/workbench/apps/knowledge'


@pytest.mark.asyncio
async def test_ragflow_subscriber_reacts_to_enterprise_hooks() -> None:
    from backend.app.hasn.service.ragflow_subscriber import RAGFlowSubscriber, RecordingRAGFlowActions

    actions = RecordingRAGFlowActions()
    subscriber = RAGFlowSubscriber(actions=actions)

    await subscriber.on_enterprise_created({'enterprise_id': 42, 'owner_user_id': 7})
    await subscriber.on_member_approved({'enterprise_id': 42, 'user_id': 8})
    await subscriber.on_member_left({'enterprise_id': 42, 'user_id': 8})
    await subscriber.on_workspace_switched({'user_id': 8})
    await subscriber.on_enterprise_disbanded({'enterprise_id': 42, 'member_user_ids': [7, 8]})

    assert actions.calls == [
        ('create_placeholder', {'enterprise_id': 42}),
        ('provision_member', {'enterprise_id': 42, 'user_id': 8}),
        ('revoke_member', {'enterprise_id': 42, 'user_id': 8}),
        ('notify_credentials_changed', {'user_id': 8}),
        ('disable_enterprise_instance', {'enterprise_id': 42, 'member_user_ids': [7, 8]}),
    ]


def test_hasn_router_exposes_enterprise_workbench_and_knowledge_routes() -> None:
    from backend.app.hasn.api.router import app, v1

    routes = {route.path for router in (v1, app) for route in router.routes}

    assert '/api/v1/hasn/enterprises' in routes
    assert '/api/v1/hasn/users/me/workspaces' in routes
    assert '/api/v1/hasn/app/workbench/apps' in routes
    # 知识库凭据路由已从 /users/me/knowledge-credentials 改名为 /knowledge/credentials（功能未变）
    assert '/api/v1/hasn/app/knowledge/credentials' in routes
    assert '/api/v1/hasn/app/knowledge/credentials/refresh' in routes


def test_workbench_app_routes_inject_database_sessions() -> None:
    from backend.app.hasn.api.router import app, v1

    affected_prefixes = (
        '/api/v1/hasn/users/me/workspaces',
        '/api/v1/hasn/enterprises',
        '/api/v1/hasn/app/workbench',
        '/api/v1/hasn/app/users/me/knowledge-credentials',
        '/api/v1/hasn/app/knowledge',
        '/api/v1/hasn/app/enterprises',
        '/api/v1/hasn/app/users/search',
    )

    checked: list[str] = []
    offenders: list[str] = []
    for route in [*v1.routes, *app.routes]:
        path = getattr(route, 'path', '')
        if not path.startswith(affected_prefixes):
            continue
        checked.append(path)
        if any(param.name == 'db' for param in route.dependant.query_params):
            offenders.append(path)

    assert checked
    assert offenders == []


@pytest.mark.asyncio
async def test_workbench_app_handlers_delegate_to_domain_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api.v1.app import workbench as module

    calls: list[tuple[str, dict]] = []

    async def list_current_workspace_apps(db: object, *, user_id: int) -> list[str]:  # noqa: RUF029
        calls.append(('current', {'db': db, 'user_id': user_id}))
        return ['knowledge']

    async def list_workbench_apps(  # noqa: RUF029
        db: object,
        *,
        user_id: int,
        workspace_kind: str | None,
    ) -> list[str]:
        calls.append(('market', {'db': db, 'user_id': user_id, 'workspace_kind': workspace_kind}))
        return ['knowledge', 'chat']

    async def enable_current_workspace_app(  # noqa: RUF029
        db: object,
        *,
        user_id: int,
        app_id: str,
    ) -> dict[str, object]:
        calls.append(('enable', {'db': db, 'user_id': user_id, 'app_id': app_id}))
        return {'app_id': app_id, 'status': 'active'}

    async def disable_current_workspace_app(  # noqa: RUF029
        db: object,
        *,
        user_id: int,
        app_id: str,
    ) -> dict[str, object]:
        calls.append(('disable', {'db': db, 'user_id': user_id, 'app_id': app_id}))
        return {'app_id': app_id, 'status': 'disabled'}

    monkeypatch.setattr(module.workbench_domain_service, 'list_current_workspace_apps', list_current_workspace_apps)
    monkeypatch.setattr(module.workbench_domain_service, 'list_workbench_apps', list_workbench_apps)
    monkeypatch.setattr(module.workbench_domain_service, 'enable_current_workspace_app', enable_current_workspace_app)
    monkeypatch.setattr(module.workbench_domain_service, 'disable_current_workspace_app', disable_current_workspace_app)

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    db = object()

    assert (await module.current_workspace_apps(request, db)).data == ['knowledge']
    assert (await module.list_workbench_apps(request, db, workspace_kind='enterprise')).data == ['knowledge', 'chat']
    assert (await module.enable_workbench_app(request, db, 'knowledge')).data == {
        'app_id': 'knowledge',
        'status': 'active',
    }
    assert (await module.disable_workbench_app(request, db, 'knowledge')).data == {
        'app_id': 'knowledge',
        'status': 'disabled',
    }
    assert calls == [
        ('current', {'db': db, 'user_id': 7}),
        ('market', {'db': db, 'user_id': 7, 'workspace_kind': 'enterprise'}),
        ('enable', {'db': db, 'user_id': 7, 'app_id': 'knowledge'}),
        ('disable', {'db': db, 'user_id': 7, 'app_id': 'knowledge'}),
    ]


@pytest.mark.asyncio
async def test_workspace_handlers_delegate_to_domain_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api.v1.app import workspace as module

    calls: list[tuple[str, dict]] = []

    async def list_user_workspaces(db: object, *, user_id: int) -> list[dict[str, object]]:  # noqa: RUF029
        calls.append(('list', {'db': db, 'user_id': user_id}))
        return [{'kind': 'personal'}]

    async def switch_active_workspace(  # noqa: RUF029
        db: object,
        *,
        user_id: int,
        kind: str,
        enterprise_id: int | None,
    ) -> dict[str, object]:
        calls.append(('switch', {'db': db, 'user_id': user_id, 'kind': kind, 'enterprise_id': enterprise_id}))
        return {'kind': kind, 'enterprise_id': enterprise_id}

    monkeypatch.setattr(module.workbench_domain_service, 'list_user_workspaces', list_user_workspaces)
    monkeypatch.setattr(module.workbench_domain_service, 'switch_active_workspace', switch_active_workspace)

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    db = object()

    assert (await module.list_my_workspaces(request, db)).data == [{'kind': 'personal'}]
    assert (
        await module.switch_active_workspace(
            request,
            db,
            module.SwitchWorkspaceRequest(kind='enterprise', enterprise_id=42),
        )
    ).data == {'active': {'kind': 'enterprise', 'enterprise_id': 42}}
    assert calls == [
        ('list', {'db': db, 'user_id': 7}),
        ('switch', {'db': db, 'user_id': 7, 'kind': 'enterprise', 'enterprise_id': 42}),
    ]


@pytest.mark.asyncio
async def test_knowledge_handlers_delegate_to_domain_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api.v1.app import knowledge as module

    calls: list[tuple[str, dict]] = []

    def record(name: str, result: object) -> Callable[..., Awaitable[object]]:
        async def inner(db: object, **kwargs: object) -> object:  # noqa: RUF029
            calls.append((name, {'db': db, **kwargs}))
            return result

        return inner

    monkeypatch.setattr(
        module.workbench_domain_service,
        'get_current_knowledge_credentials',
        record('credentials', {'status': 'active'}),
    )
    monkeypatch.setattr(
        module.workbench_domain_service,
        'refresh_current_knowledge_credentials',
        record('refresh', {'rotated': True}),
    )
    # RF-CLOUD：datasets/search/upload 数据面路由已删除（daemon 直连 RagFlow），
    # 这里只保留控制面路由（凭据 + 企业实例配置）。
    monkeypatch.setattr(
        module.workbench_domain_service,
        'get_enterprise_ragflow_instance',
        record('get_instance', {'enterprise_id': 42}),
    )
    monkeypatch.setattr(
        module.workbench_domain_service,
        'save_enterprise_ragflow_instance',
        record('save_instance', {'saved': True}),
    )
    monkeypatch.setattr(
        module.workbench_domain_service,
        'test_enterprise_ragflow_instance',
        record('test_instance', {'ok': True}),
    )
    monkeypatch.setattr(
        module.workbench_domain_service,
        'disable_enterprise_ragflow_instance',
        record('disable_instance', {'disabled': True}),
    )

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    db = object()

    assert (await module.get_knowledge_credentials(request, db)).data == {'status': 'active'}
    assert (await module.refresh_knowledge_credentials(request, db)).data == {'rotated': True}
    assert (await module.get_enterprise_ragflow_instance(request, db, enterprise_id=42)).data == {'enterprise_id': 42}
    assert (
        await module.save_enterprise_ragflow_instance(
            request,
            db,
            42,
            module.SaveRagflowInstanceRequest(
                url='https://ragflow.example',
                admin_api_key='secret',
                public_pem='pem',
                default_embd_id='embd',
                default_llm_id='llm',
            ),
        )
    ).data == {'saved': True}
    assert (await module.test_enterprise_ragflow_instance(request, db, enterprise_id=42)).data == {'ok': True}
    assert (await module.disable_enterprise_ragflow_instance(request, db, enterprise_id=42)).data == {'disabled': True}

    assert calls == [
        ('credentials', {'db': db, 'user_id': 7}),
        ('refresh', {'db': db, 'user_id': 7}),
        ('get_instance', {'db': db, 'enterprise_id': 42, 'user_id': 7}),
        (
            'save_instance',
            {
                'db': db,
                'enterprise_id': 42,
                'user_id': 7,
                'url': 'https://ragflow.example',
                'admin_api_key': 'secret',
                'public_pem': 'pem',
                'default_embd_id': 'embd',
                'default_llm_id': 'llm',
            },
        ),
        ('test_instance', {'db': db, 'enterprise_id': 42, 'user_id': 7}),
        ('disable_instance', {'db': db, 'enterprise_id': 42, 'user_id': 7}),
    ]


@pytest.mark.asyncio
async def test_enterprise_handlers_delegate_to_domain_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api.v1.app import enterprise as module

    calls: list[tuple[str, dict]] = []

    def record(name: str, result: object = None) -> Callable[..., Awaitable[object]]:
        async def inner(db: object, *args: object, **kwargs: object) -> object:  # noqa: RUF029
            payload = {'db': db, **kwargs}
            if args:
                payload['args'] = args
            calls.append((name, payload))
            return result

        return inner

    method_results = {
        'create_enterprise': {'id': 42},
        'search_enterprises': [{'id': 42}],
        'get_enterprise': {'id': 42},
        'update_enterprise': {'name': 'New'},
        'delete_enterprise': None,
        'list_members': [{'user_id': 7}],
        'apply_enterprise': {'status': 'pending'},
        'list_applications': [{'id': 5}],
        'approve_application': {'status': 'approved'},
        'reject_application': {'status': 'rejected'},
        'remove_member': None,
        'list_invite_codes': [{'code': 'JOIN'}],
        'create_invite_code': {'code': 'JOIN'},
        'revoke_invite_code': {'revoked': True},
    }
    for method, result in method_results.items():
        monkeypatch.setattr(module.workbench_domain_service, method, record(method, result))

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    db = object()
    expires_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert (
        await module.create_enterprise(
            request,
            db,
            module.CreateEnterpriseRequest(name='Acme', slug='acme', description='desc'),
        )
    ).data == {'id': 42}
    assert (await module.search_enterprises(db, q='ac')).data == [{'id': 42}]
    assert (await module.get_enterprise(db, enterprise_id=42)).data == {'id': 42}
    assert (await module.update_enterprise(db, enterprise_id=42, body={'name': 'New'})).data == {'name': 'New'}
    assert (await module.delete_enterprise(db, enterprise_id=42)).data is None
    assert (await module.list_members(db, enterprise_id=42)).data == [{'user_id': 7}]
    assert (
        await module.apply_enterprise(
            request,
            db,
            42,
            module.ApplyEnterpriseRequest(apply_message='please', invite_code='JOIN'),
        )
    ).data == {'status': 'pending'}
    assert (await module.list_applications(db, enterprise_id=42, status='approved')).data == [{'id': 5}]
    assert (await module.approve_application(request, db, enterprise_id=42, app_id=5)).data == {'status': 'approved'}
    assert (
        await module.reject_application(
            request,
            db,
            42,
            5,
            module.RejectApplicationRequest(note='no'),
        )
    ).data == {'status': 'rejected'}
    assert (await module.remove_member(db, enterprise_id=42, user_id=8)).data is None
    assert (await module.list_invite_codes(db, enterprise_id=42)).data == [{'code': 'JOIN'}]
    assert (
        await module.create_invite_code(
            request,
            db,
            42,
            module.CreateInviteCodeRequest(max_uses=3, expires_at=expires_at, auto_approve=True),
        )
    ).data == {'code': 'JOIN'}
    assert (await module.revoke_invite_code(db, enterprise_id=42, code='JOIN')).data == {'revoked': True}

    assert calls == [
        (
            'create_enterprise',
            {
                'db': db,
                'user_id': 7,
                'name': 'Acme',
                'slug': 'acme',
                'description': 'desc',
                'logo': None,
                'industry': None,
                'company_size': None,
                'join_policy': 'invite_only',
            },
        ),
        ('search_enterprises', {'db': db, 'q': 'ac'}),
        ('get_enterprise', {'db': db, 'args': (42,)}),
        ('update_enterprise', {'db': db, 'enterprise_id': 42, 'updates': {'name': 'New'}}),
        ('delete_enterprise', {'db': db, 'enterprise_id': 42}),
        ('list_members', {'db': db, 'enterprise_id': 42}),
        (
            'apply_enterprise',
            {'db': db, 'enterprise_id': 42, 'user_id': 7, 'apply_message': 'please', 'invite_code': 'JOIN'},
        ),
        ('list_applications', {'db': db, 'enterprise_id': 42, 'status': 'approved'}),
        ('approve_application', {'db': db, 'enterprise_id': 42, 'app_id': 5, 'decided_by': 7}),
        ('reject_application', {'db': db, 'enterprise_id': 42, 'app_id': 5, 'decided_by': 7, 'note': 'no'}),
        ('remove_member', {'db': db, 'enterprise_id': 42, 'user_id': 8}),
        ('list_invite_codes', {'db': db, 'enterprise_id': 42}),
        (
            'create_invite_code',
            {
                'db': db,
                'enterprise_id': 42,
                'created_by': 7,
                'max_uses': 3,
                'expires_at': expires_at,
                'auto_approve': True,
            },
        ),
        ('revoke_invite_code', {'db': db, 'enterprise_id': 42, 'code': 'JOIN'}),
    ]


@pytest.mark.asyncio
async def test_api_key_handlers_delegate_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.api.v1.app import hasn_api_keys as module
    from backend.app.hasn.schema.hasn_api_keys import CreateApiKeyReq, CreateApiKeyRes

    created_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    calls: list[tuple[str, dict]] = []

    async def list_api_keys(**kwargs: object) -> list[SimpleNamespace]:  # noqa: RUF029
        calls.append(('list', kwargs))
        return [
            SimpleNamespace(
                key_id='key1',
                key_name='Office Mac',
                owner_id='h_owner',
                status='active',
                scopes={'knowledge': ['read']},
                bound_node_id='node1',
                expires_at=None,
                created_time=created_time,
                last_used_at=None,
            )
        ]

    async def create_api_key(**kwargs: object) -> CreateApiKeyRes:  # noqa: RUF029
        calls.append(('create', kwargs))
        return CreateApiKeyRes(
            key_id='key2',
            key_name='Laptop',
            owner_id='h_owner',
            status='active',
            scopes={'message': ['read']},
            bound_node_id=None,
            expires_at=None,
            created_time=created_time,
            last_seen_at=None,
            owner_api_key='plain-once',
        )

    async def delete_api_key(**kwargs: object) -> None:  # noqa: RUF029
        calls.append(('delete', kwargs))

    class Db:
        def __init__(self) -> None:
            self.commits = 0

        async def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(module.hasn_api_key_service, 'list_api_keys', list_api_keys)
    monkeypatch.setattr(module.hasn_api_key_service, 'create_api_key', create_api_key)
    monkeypatch.setattr(module.hasn_api_key_service, 'delete_api_key', delete_api_key)

    db = Db()
    auth = {'user_id': 7, 'hasn_id': 'h_owner'}

    listed = await module.list_hasn_api_keys(db, auth)
    assert listed.data[0]['key_id'] == 'key1'
    assert listed.data[0]['last_seen_at'] is None

    created = await module.create_hasn_api_key(
        CreateApiKeyReq(name='Laptop', scopes={'message': ['read']}, bound_node_id=None),
        db,
        auth,
    )
    assert created.data['owner_api_key'] == 'plain-once'
    assert db.commits == 1

    deleted = await module.delete_hasn_api_key('key2', db, auth)
    assert deleted.data is None
    assert db.commits == 2
    assert calls == [
        ('list', {'db': db, 'user_hasn_id': 'h_owner'}),
        (
            'create',
            {
                'db': db,
                'user_id': 7,
                'user_hasn_id': 'h_owner',
                'name': 'Laptop',
                'scopes': {'message': ['read']},
                'bound_node_id': None,
                'expires_at': None,
            },
        ),
        ('delete', {'db': db, 'user_hasn_id': 'h_owner', 'key_id': 'key2'}),
    ]
