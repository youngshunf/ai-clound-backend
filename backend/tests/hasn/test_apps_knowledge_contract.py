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
)

# 应用平台 v3 P3（设计 17 决策①②）：hasn_workspace_app（挂载）与 hasn_user_active_workspace
# （重 workspace 实体）已退役删表 + model/schema/crud/service/admin-api 物理删除——
# 「应用一律开箱即用」+「身份上下文收缩为 hasn_owner_workbench_pref.active_enterprise_id 瘦指针」。
LEGACY_WORKSPACE_TABLES = (
    'hasn_user_active_workspace',
    'hasn_workspace_app',
)

# 实施 14-AI-Native应用平台/实施/03 收编：知识库（RAGFlow）实例/凭据已并入统一应用平台
# 底座 hasn_app_instance + hasn_app_credential。这两张表无 per-table service（控制面走
# instance_resolver + workbench_domain_service），故只校验 sql/model/schema/crud 四件套。
CONSOLIDATED_APP_TABLES = (
    'hasn_app_instance',
    'hasn_app_credential',
)

# P5 DoD：旧两套表的 model/schema/crud/service/sql 必须物理删除（「只剩一套」）。
LEGACY_RAGFLOW_TABLES = (
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

    for table in CONSOLIDATED_APP_TABLES:
        expected = {
            REPO_ROOT / 'backend' / 'sql' / 'hasn' / f'{table}.sql',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / f'crud_{table}.py',
        }
        missing.extend(path.relative_to(REPO_ROOT).as_posix() for path in expected if not path.exists())

    assert missing == []


def test_legacy_ragflow_tables_fully_removed() -> None:
    """实施 03 P5 DoD：旧 hasn_ragflow_instance/credential 三件套 + SQL 已物理删除。"""
    leftover: list[str] = []
    for table in LEGACY_RAGFLOW_TABLES:
        candidates = {
            REPO_ROOT / 'backend' / 'sql' / 'hasn' / f'{table}.sql',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / f'crud_{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / f'{table}_service.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'api' / 'v1' / 'admin' / f'{table}.py',
        }
        leftover.extend(path.relative_to(REPO_ROOT).as_posix() for path in candidates if path.exists())

    assert leftover == []


def test_legacy_workspace_tables_fully_removed() -> None:
    """应用平台 v3 P3 DoD：hasn_workspace_app / hasn_user_active_workspace 的
    sql/model/schema/crud/service/admin-api 全部物理删除（挂载 + 重 workspace 实体退役）。"""
    leftover: list[str] = []
    for table in LEGACY_WORKSPACE_TABLES:
        candidates = {
            REPO_ROOT / 'backend' / 'sql' / 'hasn' / f'{table}.sql',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / f'{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / f'crud_{table}.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / f'{table}_service.py',
            REPO_ROOT / 'backend' / 'app' / 'hasn' / 'api' / 'v1' / 'admin' / f'{table}.py',
        }
        leftover.extend(path.relative_to(REPO_ROOT).as_posix() for path in candidates if path.exists())

    assert leftover == []


def test_workbench_codegen_schemas_validate_workspace_and_instance_invariants() -> None:
    from backend.app.hasn.schema.hasn_app_credential import CreateHasnAppCredentialParam
    from backend.app.hasn.schema.hasn_app_instance import CreateHasnAppInstanceParam
    from backend.app.hasn.schema.hasn_enterprise import CreateHasnEnterpriseParam
    from backend.app.hasn.schema.hasn_enterprise_invite_code import CreateHasnEnterpriseInviteCodeParam
    from backend.app.hasn.schema.hasn_enterprise_membership import CreateHasnEnterpriseMembershipParam

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
    # 实施 03 收编：知识库实例/凭据改用通用应用平台底座 schema；RAGFlow 私有字段
    # （public_pem / ragflow_user_id / ragflow_tenant_id）下沉 config。
    instance = CreateHasnAppInstanceParam(
        app_id='knowledge',
        scope='enterprise',
        enterprise_id=1,
        endpoint='https://knowledge.example',
        transport_default='daemon_direct',
        credential_ref='enc:admin-key',
        status='active',
        config={'public_pem': 'pem', 'default_embd_id': 'embd', 'default_llm_id': 'llm'},
    )
    credential = CreateHasnAppCredentialParam(
        app_id='knowledge',
        user_id=8,
        app_instance_id=1,
        credential_ref='enc:user-key',
        status='pending',
        config={'ragflow_user_id': 'rf-user', 'ragflow_tenant_id': 'rf-tenant'},
    )

    assert enterprise.status == 'active'
    assert membership.status == 'pending'
    assert invite.used_count == 0
    assert instance.app_id == 'knowledge'
    assert instance.config['public_pem'] == 'pem'
    assert credential.config['ragflow_user_id'] == 'rf-user'
    assert credential.status == 'pending'


def test_workbench_codegen_admin_api_modules_import_and_mount() -> None:
    from backend.app.hasn.api.router import v1

    routes = {route.path for route in v1.routes}

    assert '/api/v1/hasn/enterprises' in routes
    assert '/api/v1/hasn/enterprise/memberships' in routes
    assert '/api/v1/hasn/enterprise/invite-codes' in routes
    # 应用平台 v3 P3（设计 17 决策①②）：挂载 + 重 workspace 实体退役，两套 admin 路由随表删除。
    assert '/api/v1/hasn/user/active-workspaces' not in routes
    assert '/api/v1/hasn/workspace/apps' not in routes
    # 实施 03 P5：旧 /ragflow/instances、/ragflow/credentials admin 路由已随收编删除
    # （知识库凭据走用户端 /api/v1/hasn/app/knowledge/credentials，由 instance_resolver 选实例）。
    assert '/api/v1/hasn/ragflow/instances' not in routes
    assert '/api/v1/hasn/ragflow/credentials' not in routes


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
    from backend.app.hasn.service.app_catalog_registry import AppCatalogRegistry

    registry = AppCatalogRegistry.default()

    # 内置应用均 install_policy=auto；按 scope 过滤自动安装：
    # - deck（自研演示文稿，模块 17）scope=personal，唯一默认演示文稿应用（presentation 已删除）；
    # - hasn_task scope=(personal, enterprise)，两空间皆自动安装；
    # - publish scope=personal，仅 personal 自动安装。
    assert [app.id for app in registry.auto_install_apps('personal')] == [
        'knowledge',
        'community',
        'deck',
        'hasn_task',
        'publish',
    ]
    assert [app.id for app in registry.auto_install_apps('enterprise')] == ['knowledge', 'community', 'hasn_task']
    assert registry.get('knowledge').entry_route == '/apps/knowledge'


def test_hasn_router_exposes_enterprise_workbench_and_knowledge_routes() -> None:
    from backend.app.hasn.api.router import app, v1

    # ADR-15 批次3：工作台 API 抽出独立模块 backend.app.home（schema hasn_workbench），
    # 路由器 home_app 仍挂同一 prefix /api/v1/hasn/app（节点无感知）。
    from backend.app.home.api.router import home_app

    routes = {route.path for router in (v1, app, home_app) for route in router.routes}

    assert '/api/v1/hasn/enterprises' in routes
    assert '/api/v1/hasn/users/me/workspaces' in routes
    assert '/api/v1/hasn/app/apps' in routes
    # 凭据下发面已随知识库 AI-Native 重做退役（设计 §7.1）：凭据=平台 service key 只活云端
    assert '/api/v1/hasn/app/knowledge/credentials' not in routes
    assert '/api/v1/hasn/app/knowledge/credentials/refresh' not in routes
    # 知识库数据面归位独立模块 /api/v1/knowledge/*（app/hasn_knowledge）
    from backend.app.hasn_knowledge.api.router import agent as knowledge_agent
    from backend.app.hasn_knowledge.api.router import app as knowledge_app

    knowledge_routes = {route.path for router in (knowledge_app, knowledge_agent) for route in router.routes}
    assert '/api/v1/knowledge/app/kbs' in knowledge_routes
    assert '/api/v1/knowledge/app/search' in knowledge_routes
    assert '/api/v1/knowledge/agent/search' in knowledge_routes


def test_workbench_app_routes_inject_database_sessions() -> None:
    from backend.app.hasn.api.router import app, v1

    affected_prefixes = (
        '/api/v1/hasn/users/me/workspaces',
        '/api/v1/hasn/enterprises',
        '/api/v1/hasn/app/home',
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
    """应用平台 v3 P3（设计 17 决策①）：挂载废除——current/enable/disable workspace-app
    端点已删除，工作台只剩 list_apps（catalog ∩ entitlement，开箱即用）。
    （ADR-15 批次3：工作台 handler 已迁 backend.app.home.api.v1.app.home。）"""
    from backend.app.home.api.v1.app import home as module

    calls: list[tuple[str, dict]] = []

    async def list_apps(  # noqa: RUF029
        db: object,
        *,
        user_id: int,
    ) -> list[str]:
        # 应用平台 v3（去工作空间绑定）：工作台清单与激活空间无关，handler 不再透传 workspace_kind。
        calls.append(('market', {'db': db, 'user_id': user_id}))
        return ['knowledge', 'chat']

    monkeypatch.setattr(module.workbench_domain_service, 'list_apps', list_apps)

    request = SimpleNamespace(user=SimpleNamespace(id=7))
    db = object()

    assert (await module.list_apps(request, db)).data == ['knowledge', 'chat']
    assert calls == [
        ('market', {'db': db, 'user_id': 7}),
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

    # 凭据下发面已退役（设计 §7.1）；本测试只覆盖暂留的企业实例登记面（DEPRECATED，P3 重评）。
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
