from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ai_native_codegen_and_migration_foundation_exist() -> None:
    expected = {
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'migration' / 'v12_ai_native_app_platform.sql',
        REPO_ROOT / 'backend' / 'sql' / 'hasn' / 'hasn_ai_native_app_manifest.sql',
        REPO_ROOT / 'backend' / 'sql' / 'hasn' / 'hasn_ai_native_app_audit.sql',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / 'hasn_ai_native_app_manifest.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'model' / 'hasn_ai_native_app_audit.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / 'crud_hasn_ai_native_app_manifest.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'crud' / 'crud_hasn_ai_native_app_audit.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / 'ai_native_app.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / 'ai_native_runtime.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'schema' / 'ai_native_audit.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / 'ai_native_builtin_manifests.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / 'ai_native_app_registry.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / 'ai_native_runtime_gateway.py',
        REPO_ROOT / 'backend' / 'app' / 'hasn' / 'service' / 'ai_native_audit_service.py',
    }

    missing = [path.relative_to(REPO_ROOT).as_posix() for path in expected if not path.exists()]

    assert missing == []


def test_legacy_app_platform_backend_surface_is_removed() -> None:
    legacy_paths = {
        REPO_ROOT / 'backend' / 'app' / 'app_platform',
        REPO_ROOT / 'backend' / 'tests' / 'app_platform',
    }

    remaining = [path.relative_to(REPO_ROOT).as_posix() for path in legacy_paths if path.exists()]

    assert remaining == []


def test_mcp_app_tools_surface_no_longer_depends_on_legacy_app_platform() -> None:
    source = (REPO_ROOT / 'backend' / 'app' / 'mcp' / 'tools' / 'app_tools.py').read_text(encoding='utf-8')

    assert 'backend.app.app_platform' not in source


def test_builtin_knowledge_manifest_matches_p0_contract() -> None:
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST

    manifest = KNOWLEDGE_AI_NATIVE_MANIFEST

    assert manifest['app_id'] == 'knowledge'
    assert manifest['version'] == '1.0.0'
    assert manifest['workspace_scope'] == ['personal', 'enterprise']
    assert manifest['collaboration_mode'] == 'workspace_shared'
    assert manifest['capabilities'][0]['tool_id'] == 'knowledge.search'
    assert manifest['capabilities'][0]['mcp_name'] == 'hasn.knowledge.search'
    # P1 词表迁移（点→冒号，#1079）：scope 统一冒号分隔。
    assert manifest['capabilities'][0]['required_scopes'] == ['knowledge:read']
    assert manifest['tools'][0]['handler'] == 'knowledge.search'
    assert manifest['tools'][0]['idempotent'] is True


def test_manifest_validator_accepts_builtin_knowledge_manifest() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    registry = AINativeAppRegistry(workbench_registry=workbench_app_registry)

    result = registry.validate_manifest(KNOWLEDGE_AI_NATIVE_MANIFEST)

    assert result.valid is True
    assert result.errors == []
    assert result.manifest_hash.startswith('sha256:')


def test_manifest_validator_rejects_unknown_workbench_app() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry

    registry = AINativeAppRegistry()
    manifest = {
        'app_id': 'unknown',
        'version': '1.0.0',
        'workspace_scope': ['personal'],
        'collaboration_mode': 'none',
        'capabilities': [],
        'tools': [],
        'events': [],
        'reverse_invoke': {'supported': False},
        'audit': {'fields': ['trace_id', 'workspace', 'app_id', 'decision']},
    }

    result = registry.validate_manifest(manifest)

    assert result.valid is False
    assert 'workbench_app_not_found' in result.errors


def test_manifest_validator_rejects_scope_and_collaboration_drift() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp, WorkbenchAppRegistry

    workbench = WorkbenchAppRegistry()
    workbench.register(
        WorkbenchApp(
            id='knowledge',
            name='知识库',
            icon='book-open',
            description='个人知识库',
            scope=('personal',),
            collaboration_mode='none',
            entry_route='/workbench/apps/knowledge',
            install_policy='auto',
        )
    )
    registry = AINativeAppRegistry(workbench_registry=workbench)

    result = registry.validate_manifest(KNOWLEDGE_AI_NATIVE_MANIFEST)

    assert result.valid is False
    assert 'workspace_scope_exceeds_workbench_scope' in result.errors
    assert 'collaboration_mode_mismatch' in result.errors


def test_hasn_router_mounts_ai_native_app_routes() -> None:
    from backend.app.hasn.api.router import ai_native

    routes = {route.path for route in ai_native.routes}

    assert '/api/v1/ai-native/apps' in routes
    assert '/api/v1/ai-native/apps/{app_id}' in routes
    assert '/api/v1/ai-native/apps/{app_id}/validate' in routes
    assert '/api/v1/ai-native/apps/{app_id}/publish' in routes


@pytest.mark.asyncio
async def test_publish_builtin_manifest_uses_published_status() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.workbench_app_registry import workbench_app_registry

    registry = AINativeAppRegistry(workbench_registry=workbench_app_registry)
    saved = await registry.publish_builtin(None, 'knowledge')

    assert saved['app_id'] == 'knowledge'
    assert saved['status'] == 'published'
    assert saved['manifest_json'] == KNOWLEDGE_AI_NATIVE_MANIFEST
    assert saved['manifest_hash'].startswith('sha256:')


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDb:
    def __init__(
        self,
        *,
        workspace: dict[str, Any] | None,
        app_row: Any = None,
        audit_rows: list[Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.app_row = app_row
        self.audit_rows = list(audit_rows or [])
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _ScalarResult:
        sql = str(stmt)
        if 'hasn_workspace_app' in sql:
            return _ScalarResult([self.app_row] if self.app_row is not None else [])
        if 'hasn_ai_native_app_audit' in sql:
            rows = list(self.audit_rows)
            params = getattr(stmt.compile(), 'params', {})
            if 'workspace_kind_1' in params:
                rows = [row for row in rows if row.workspace_kind == params['workspace_kind_1']]
            if 'app_id_1' in params:
                rows = [row for row in rows if row.app_id == params['app_id_1']]
            if 'agent_hasn_id_1' in params:
                rows = [row for row in rows if row.agent_hasn_id == params['agent_hasn_id_1']]
            if 'trace_id_1' in params:
                rows = [row for row in rows if row.trace_id == params['trace_id_1']]
            created_at_from = params.get('created_at_1')
            created_at_to = params.get('created_at_2')
            if created_at_from is not None:
                rows = [row for row in rows if row.created_at >= created_at_from]
            if created_at_to is not None:
                rows = [row for row in rows if row.created_at <= created_at_to]
            return _ScalarResult(rows)
        return _ScalarResult([])

    def add(self, row: Any) -> None:
        self.added.append(row)
        if getattr(row, 'id', None) is None:
            row.id = len(self.added)

    async def flush(self) -> None:
        return None

    async def refresh(self, row: Any) -> None:
        if getattr(row, 'id', None) is None:
            row.id = len(self.added)


class _FakeAgent:
    agent_hasn_id = 'a_001'
    agent_name = 'Agent'
    owner_hasn_id = 'h_001'
    owner_user_id = 12345
    scopes = ['knowledge.read']
    session_uuid = 'session-001'


class _FakeMembership:
    def __init__(self, *, role: str) -> None:
        self.role = role


def _knowledge_manifest_payload(
    *,
    collaboration_mode: str = 'workspace_shared',
    workspace_roles: list[str] | None = None,
) -> dict[str, Any]:
    from backend.app.hasn.service.ai_native_app_registry import _manifest_hash
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.utils.timezone import timezone

    manifest = deepcopy(KNOWLEDGE_AI_NATIVE_MANIFEST)
    manifest['collaboration_mode'] = collaboration_mode
    manifest['capabilities'][0]['workspace_roles'] = workspace_roles or ['owner', 'admin', 'member']
    return {
        'id': None,
        'app_id': manifest['app_id'],
        'version': manifest['version'],
        'status': 'published',
        'workspace_scope': list(manifest.get('workspace_scope') or []),
        'collaboration_mode': collaboration_mode,
        'manifest_json': manifest,
        'manifest_hash': _manifest_hash(manifest),
        'published_at': timezone.now(),
    }


def _community_manifest_payload() -> dict[str, Any]:
    from backend.app.hasn.service.ai_native_app_registry import _manifest_hash
    from backend.app.hasn.service.ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST
    from backend.utils.timezone import timezone

    manifest = deepcopy(COMMUNITY_AI_NATIVE_MANIFEST)
    return {
        'id': None,
        'app_id': manifest['app_id'],
        'version': manifest['version'],
        'status': 'published',
        'workspace_scope': list(manifest.get('workspace_scope') or []),
        'collaboration_mode': manifest['collaboration_mode'],
        'manifest_json': manifest,
        'manifest_hash': _manifest_hash(manifest),
        'published_at': timezone.now(),
    }


def _make_runtime_test_app(
    fake_db: _FakeDb,
    monkeypatch: pytest.MonkeyPatch,
    *,
    patch_agent: bool = True,
) -> FastAPI:
    from backend.app.hasn.api.v1 import ai_native_app as module
    from backend.database.db import get_db, get_db_transaction

    app = FastAPI()
    app.include_router(module.runtime_router, prefix='/api/v1/ai-native/runtime')
    app.include_router(module.audit_router, prefix='/api/v1/ai-native/audit')

    async def fake_agent_auth() -> None:
        return None

    async def fake_db_session():
        yield fake_db

    async def fake_db_transaction():
        yield fake_db

    async def fake_runtime_agent(_request):
        return {'decision': 'allow', 'agent': _FakeAgent()}

    if patch_agent:
        app.dependency_overrides[module.DependsAgentJwtAuth.dependency] = fake_agent_auth
    app.dependency_overrides[get_db] = fake_db_session
    app.dependency_overrides[get_db_transaction] = fake_db_transaction
    if patch_agent:
        monkeypatch.setattr(module.ai_native_runtime_gateway, '_require_agent', lambda _request: _FakeAgent())
        monkeypatch.setattr(
            module.ai_native_runtime_gateway,
            '_authenticate_runtime_agent',
            fake_runtime_agent,
        )

    # 维度① 三态能力授权（D3 活取）：gateway 经 get_agent_scopes_cached 取 hasn_agent_scopes 策略，
    # 不再读 key/JWT 冻结的 scopes 快照。这里默认全开（default_mode=allow），deny 用例自行 override。
    import backend.common.security.agent_jwt as agent_jwt_module

    async def _default_allow_scopes(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {'default_mode': 'allow', 'capability_modes': {}, 'scopes': [], 'post_needs_review': False}

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _default_allow_scopes)
    return app


def test_runtime_capabilities_returns_current_workspace_knowledge_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        assert user_id == 12345
        return {'kind': 'personal', 'enterprise_id': None}

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/capabilities',
            json={'workspace': None, 'include_disabled': False, 'trace_id': 'trace-1'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['workspace'] == {
        'kind': 'personal',
        'user_id': 12345,
        'enterprise_id': None,
        'workspace_key': 'personal:12345',
    }
    assert data['tools'][0]['tool_id'] == 'knowledge.search'
    assert data['tools'][0]['mcp_name'] == 'hasn.knowledge.search'
    # P1 词表迁移（点→冒号，#1079）：scope 统一冒号分隔。
    assert data['tools'][0]['required_scopes'] == ['knowledge:read']


def test_runtime_capabilities_filters_disabled_workspace_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='disabled',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/capabilities',
            json={'workspace': None, 'include_disabled': False, 'trace_id': 'trace-disabled-discovery'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()['data']['tools'] == []


def test_enterprise_runtime_capabilities_filter_by_workspace_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=HasnWorkspaceApp(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_membership(_db: Any, *, enterprise_id: int, user_id: int) -> _FakeMembership:
        assert (enterprise_id, user_id) == (7, 12345)
        return _FakeMembership(role='member')

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _knowledge_manifest_payload(workspace_roles=['owner', 'admin'])

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/capabilities',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'include_disabled': False,
                'trace_id': 'trace-enterprise-role-filter',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['workspace'] == {
        'kind': 'enterprise',
        'user_id': None,
        'enterprise_id': 7,
        'workspace_key': 'enterprise:7',
    }
    assert data['tools'] == []


def test_enterprise_runtime_capabilities_filters_collaboration_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=HasnWorkspaceApp(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_membership(_db: Any, *, enterprise_id: int, user_id: int) -> _FakeMembership:
        assert (enterprise_id, user_id) == (7, 12345)
        return _FakeMembership(role='admin')

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _knowledge_manifest_payload(collaboration_mode='none')

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/capabilities',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'include_disabled': False,
                'trace_id': 'trace-enterprise-collaboration-filter',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()['data']['tools'] == []


def test_runtime_tool_call_invokes_real_knowledge_search_and_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        assert user_id == 12345
        return {'kind': 'personal', 'enterprise_id': None}

    async def fake_search(_db: Any, *, user_id: int, query: str, limit: int, dataset_id: str | None) -> dict[str, Any]:
        assert (user_id, query, limit, dataset_id) == (12345, '唤星工作台', 10, None)
        return {'items': [{'id': 'chunk-1', 'content': query}], 'total': 1}

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'search_current_knowledge', fake_search)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={
                'workspace': None,
                'input': {'query': '唤星工作台', 'limit': 10, 'dataset_id': None},
                'trace_id': 'trace-2',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'allow'
    assert data['result']['items'] == [{'id': 'chunk-1', 'content': '唤星工作台'}]
    audit_row = fake_db.added[-1]
    assert data['audit_id'] == audit_row.id
    assert audit_row.trace_id == 'trace-2'
    assert audit_row.decision == 'allow'


def test_runtime_tool_call_capability_deny_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    # 三态模型（D1/D3）：缺 scope 不再 deny（默认 allow）；owner 把能力显式设 deny 才硬拦 15012。
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    # owner 把 knowledge:read 能力设 deny（三态活取，冒号词表与 manifest required_scopes 一致）→ knowledge.search 被拒。
    import backend.common.security.agent_jwt as agent_jwt_module

    async def _deny_knowledge(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'knowledge:read': 'deny'},
            'scopes': [],
            'post_needs_review': False,
        }

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _deny_knowledge)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={'workspace': None, 'input': {'query': '唤星工作台'}, 'trace_id': 'trace-3'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15012', 'message': 'agent_scope_missing'}
    audit_row = fake_db.added[-1]
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15012'


def _knowledge_fake_db() -> _FakeDb:
    from backend.app.hasn.model import HasnWorkspaceApp

    return _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )


def test_runtime_tool_call_ask_mode_approved_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    # 三态 ask（owner 设「每次问我」）：runtime 网关也阻塞等批准（与 MCP 一致）。批准→执行 allow。
    import backend.app.mcp.ask_gate as ask_gate_module
    import backend.common.security.agent_jwt as agent_jwt_module

    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _knowledge_fake_db()
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    async def fake_search(_db: Any, *, user_id: int, query: str, limit: int, dataset_id: str | None) -> dict[str, Any]:
        return {'items': [], 'total': 0}

    async def _ask(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'knowledge:read': 'ask'},
            'scopes': [],
            'post_needs_review': False,
        }

    gate_calls: dict[str, Any] = {}

    async def _gate_approved(*, agent_hasn_id: str, owner_hasn_id: str | None, tool_name: str, arguments: dict) -> None:
        gate_calls['tool_name'] = tool_name  # 批准（不抛）

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _ask)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'gate', _gate_approved)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'search_current_knowledge', fake_search)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={'workspace': None, 'input': {'query': '唤星'}, 'trace_id': 'trace-ask-ok'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'allow'
    assert gate_calls['tool_name'] == 'hasn.knowledge.search'  # 确实挂起了 ask 闸门
    assert fake_db.added[-1].decision == 'allow'


def test_runtime_tool_call_ask_mode_rejected_writes_15013(monkeypatch: pytest.MonkeyPatch) -> None:
    # ask 被主人拒绝/超时（gate raise PermissionError）→ 15013 deny + 审计，不触达 handler。
    import backend.app.mcp.ask_gate as ask_gate_module
    import backend.common.security.agent_jwt as agent_jwt_module

    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _knowledge_fake_db()
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    async def _ask(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'knowledge:read': 'ask'},
            'scopes': [],
            'post_needs_review': False,
        }

    async def _gate_rejected(*, agent_hasn_id: str, owner_hasn_id: str | None, tool_name: str, arguments: dict) -> None:
        raise PermissionError('Owner approval required (ask mode, rejected)')

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _ask)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'gate', _gate_rejected)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={'workspace': None, 'input': {'query': '唤星'}, 'trace_id': 'trace-ask-no'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15013', 'message': 'agent_capability_ask_denied'}
    assert fake_db.added[-1].error_code == '15013'


def test_runtime_tool_call_community_get_post_returns_full_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    class CommunityAgent(_FakeAgent):
        scopes = ['community.read']

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='community',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_runtime_agent(_request):
        return {'decision': 'allow', 'agent': CommunityAgent()}

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        assert user_id == 12345
        return {'kind': 'personal', 'enterprise_id': None}

    async def fake_manifest(_db: Any, app_id: str) -> dict[str, Any]:
        assert app_id == 'community'
        return _community_manifest_payload()

    async def fake_get_agent_post(_db: Any, *, agent: Any, post_id: str) -> dict[str, Any]:
        assert agent.agent_hasn_id == 'a_001'
        assert post_id == 'post_01J'
        return {
            'resource': {
                'type': 'community.post',
                'id': 'post_01J',
                'app_id': 'community',
                'uri': 'hasn://app/community/posts/post_01J',
            },
            'summary': '卡片摘要',
            'content': '完整正文',
        }

    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)
    monkeypatch.setattr(gateway_module.ai_native_runtime_gateway, '_authenticate_runtime_agent', fake_runtime_agent)
    monkeypatch.setattr(gateway_module.community_service, 'get_agent_post_resource', fake_get_agent_post)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={'workspace': None, 'input': {'post_id': 'post_01J'}, 'trace_id': 'trace-community-post'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'allow'
    assert data['result']['content'] == '完整正文'
    assert data['result']['resource']['uri'] == 'hasn://app/community/posts/post_01J'
    audit_row = fake_db.added[-1]
    assert audit_row.app_id == 'community'
    assert audit_row.tool_id == 'community.get_post'
    assert audit_row.required_scopes == ['community.read']


def test_runtime_tool_call_community_capability_deny_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    # 三态模型（D1/D3）：owner 把 community:read 能力设 deny → community.get_article 被拒 15012。
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='community',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    async def fake_manifest(_db: Any, app_id: str) -> dict[str, Any]:
        assert app_id == 'community'
        return _community_manifest_payload()

    import backend.common.security.agent_jwt as agent_jwt_module

    async def _deny_community(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'community:read': 'deny'},
            'scopes': [],
            'post_needs_review': False,
        }

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _deny_community)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_article/call',
            json={'workspace': None, 'input': {'article_id': 'art_01J'}, 'trace_id': 'trace-community-deny'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15012', 'message': 'agent_scope_missing'}
    audit_row = fake_db.added[-1]
    assert audit_row.app_id == 'community'
    assert audit_row.tool_id == 'community.get_article'
    assert audit_row.error_code == '15012'


def test_runtime_tool_call_disabled_app_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='disabled',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={'workspace': None, 'input': {'query': '唤星工作台'}, 'trace_id': 'trace-disabled'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    # 设计 11 §4.2/§4.3：去掉安装态后 15002 语义改为"App 被企业 override 显式 disabled"
    # （workspace_app 记录 status=disabled 才拒；默认无记录即可用，published 即用）。
    assert data['error'] == {'code': '15002', 'message': 'app_disabled_by_enterprise'}
    audit_row = fake_db.added[-1]
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15002'


def test_enterprise_runtime_tool_call_role_denial_writes_15004_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=HasnWorkspaceApp(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_membership(_db: Any, *, enterprise_id: int, user_id: int) -> _FakeMembership:
        assert (enterprise_id, user_id) == (7, 12345)
        return _FakeMembership(role='member')

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _knowledge_manifest_payload(workspace_roles=['owner', 'admin'])

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'query': '唤星工作台'},
                'trace_id': 'trace-enterprise-role-denied',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15004', 'message': 'enterprise_role_insufficient'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-enterprise-role-denied'
    assert audit_row.workspace_kind == 'enterprise'
    assert audit_row.enterprise_id == 7
    assert audit_row.workspace_role == 'member'
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15004'
    assert audit_row.context == {'reason': 'enterprise_role_insufficient'}


def test_enterprise_runtime_tool_call_collaboration_denial_writes_15005_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=HasnWorkspaceApp(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_membership(_db: Any, *, enterprise_id: int, user_id: int) -> _FakeMembership:
        assert (enterprise_id, user_id) == (7, 12345)
        return _FakeMembership(role='admin')

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _knowledge_manifest_payload(collaboration_mode='none')

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'query': '唤星工作台'},
                'trace_id': 'trace-enterprise-collaboration-denied',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15005', 'message': 'app_not_support_enterprise_collaboration'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-enterprise-collaboration-denied'
    assert audit_row.workspace_kind == 'enterprise'
    assert audit_row.enterprise_id == 7
    assert audit_row.workspace_role == 'admin'
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15005'
    assert audit_row.context == {'reason': 'app_not_support_enterprise_collaboration'}


def test_runtime_tool_call_invalid_input_writes_15020_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        return {'kind': 'personal', 'enterprise_id': None}

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={
                'workspace': None,
                'input': {'query': '', 'limit': 0},
                'trace_id': 'trace-invalid-input',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15020', 'message': 'input_schema_invalid'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-invalid-input'
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15020'
    assert audit_row.context == {'reason': 'input_schema_invalid'}


@pytest.mark.asyncio
async def test_runtime_gateway_revoked_agent_session_writes_15011_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.schema.ai_native_runtime import AiNativeToolCallRequest
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module
    from backend.common.security import agent_jwt as agent_jwt_module
    from backend.common.security.agent_jwt import jwt_encode_agent

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    token = jwt_encode_agent(
        {
            'sub': 'a_001',
            'token_type': 'agent',
            'agent_hasn_id': 'a_001',
            'agent_name': 'Agent',
            'owner_hasn_id': 'h_001',
            'owner_user_id': 12345,
            'scopes': ['knowledge.read'],
            'session_uuid': 'session-revoked',
            'exp': datetime.now(timezone.utc).timestamp() + 3600,
        }
    )

    class MissingAgentSessionStore:
        async def get(self, _key: str) -> None:
            return None

    async def fake_active_workspace(_db: Any, *, user_id: int) -> dict[str, Any]:
        assert user_id == 12345
        return {'kind': 'personal', 'enterprise_id': None}

    missing_session_store = MissingAgentSessionStore()
    monkeypatch.setattr(agent_jwt_module, 'redis_client', missing_session_store)
    monkeypatch.setattr(gateway_module, 'redis_client', missing_session_store, raising=False)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    class RequestWithRevokedToken:
        headers = {'Authorization': f'Bearer {token}'}

        class State:
            pass

        state = State()

    data = await gateway_module.ai_native_runtime_gateway.call_tool(
        fake_db,
        request=RequestWithRevokedToken(),
        app_id='knowledge',
        tool_id='knowledge.search',
        body=AiNativeToolCallRequest(
            workspace=None,
            input={'query': '唤星工作台'},
            trace_id='trace-revoked-session',
        ),
    )
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15011', 'message': 'agent_token_session_revoked'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-revoked-session'
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15011'
    assert audit_row.agent_hasn_id == 'a_001'
    assert audit_row.session_uuid == 'session-revoked'
    assert audit_row.context == {'reason': 'agent_token_session_revoked'}


def test_runtime_tool_call_requires_agent_jwt_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=HasnWorkspaceApp(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch, patch_agent=False)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={'workspace': None, 'input': {'query': '唤星工作台'}, 'trace_id': 'trace-missing-agent-jwt'},
        )

    assert resp.status_code == 401, resp.text
    assert fake_db.added == []


def test_runtime_tool_call_inaccessible_workspace_writes_15003_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnWorkspaceApp
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=HasnWorkspaceApp(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='knowledge',
            status='active',
            config={},
            enabled_by=12345,
        ),
    )
    app = _make_runtime_test_app(fake_db, monkeypatch)

    async def missing_membership(_db: Any, *, enterprise_id: int, user_id: int) -> None:
        assert (enterprise_id, user_id) == (7, 12345)
        return

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', missing_membership)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/knowledge/knowledge.search/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'query': '唤星工作台'},
                'trace_id': 'trace-workspace-denied',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    assert data['error'] == {'code': '15003', 'message': 'workspace_inaccessible'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-workspace-denied'
    assert audit_row.workspace_kind == 'enterprise'
    assert audit_row.enterprise_id == 7
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15003'
    assert audit_row.context == {'reason': 'workspace_inaccessible'}


def test_runtime_audit_route_applies_query_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnAiNativeAppAudit

    matching_row = HasnAiNativeAppAudit(
        trace_id='trace-1',
        step='runtime',
        workspace_kind='personal',
        user_id=12345,
        enterprise_id=None,
        app_id='knowledge',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_001',
        owner_hasn_id='h_001',
        session_uuid='session-001',
        method='tool_call',
        capability_id='knowledge.search.capability',
        tool_id='knowledge.search',
        event_type='tool_call',
        required_scopes=['knowledge.read'],
        agent_scopes_snapshot=['knowledge.read'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref='knowledge:knowledge.search:trace-1',
        error_code=None,
        context={},
    )
    matching_row.id = 42
    other_row = HasnAiNativeAppAudit(
        trace_id='trace-2',
        step='runtime',
        workspace_kind='enterprise',
        user_id=None,
        enterprise_id=7,
        app_id='knowledge',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_002',
        owner_hasn_id='h_002',
        session_uuid='session-002',
        method='tool_call',
        capability_id='knowledge.search.capability',
        tool_id='knowledge.search',
        event_type='tool_call',
        required_scopes=['knowledge.read'],
        agent_scopes_snapshot=['knowledge.read'],
        workspace_role='member',
        risk_level='low',
        decision='deny',
        confirmation_id=None,
        result_ref=None,
        error_code='15012',
        context={},
    )
    other_row.id = 43
    fake_db = _FakeDb(workspace=None, audit_rows=[matching_row, other_row])
    app = _make_runtime_test_app(fake_db, monkeypatch)

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/ai-native/audit',
            params={'workspace_kind': 'personal', 'app_id': 'knowledge', 'agent_hasn_id': 'a_001'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['total'] == 1
    assert data['items'][0]['trace_id'] == 'trace-1'


def test_runtime_audit_route_applies_created_at_range_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.model import HasnAiNativeAppAudit

    older_row = HasnAiNativeAppAudit(
        trace_id='trace-old',
        step='runtime',
        workspace_kind='personal',
        user_id=12345,
        enterprise_id=None,
        app_id='knowledge',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_001',
        owner_hasn_id='h_001',
        session_uuid='session-001',
        method='tool_call',
        capability_id='knowledge.search.capability',
        tool_id='knowledge.search',
        event_type='tool_call',
        required_scopes=['knowledge.read'],
        agent_scopes_snapshot=['knowledge.read'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref='knowledge:knowledge.search:trace-old',
        error_code=None,
        context={},
    )
    older_row.id = 40
    older_row.created_at = datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc)

    matching_row = HasnAiNativeAppAudit(
        trace_id='trace-in-range',
        step='runtime',
        workspace_kind='personal',
        user_id=12345,
        enterprise_id=None,
        app_id='knowledge',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_001',
        owner_hasn_id='h_001',
        session_uuid='session-001',
        method='tool_call',
        capability_id='knowledge.search.capability',
        tool_id='knowledge.search',
        event_type='tool_call',
        required_scopes=['knowledge.read'],
        agent_scopes_snapshot=['knowledge.read'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref='knowledge:knowledge.search:trace-in-range',
        error_code=None,
        context={},
    )
    matching_row.id = 41
    matching_row.created_at = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)

    newer_row = HasnAiNativeAppAudit(
        trace_id='trace-new',
        step='runtime',
        workspace_kind='personal',
        user_id=12345,
        enterprise_id=None,
        app_id='knowledge',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_001',
        owner_hasn_id='h_001',
        session_uuid='session-001',
        method='tool_call',
        capability_id='knowledge.search.capability',
        tool_id='knowledge.search',
        event_type='tool_call',
        required_scopes=['knowledge.read'],
        agent_scopes_snapshot=['knowledge.read'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref='knowledge:knowledge.search:trace-new',
        error_code=None,
        context={},
    )
    newer_row.id = 42
    newer_row.created_at = datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc)

    fake_db = _FakeDb(workspace=None, audit_rows=[older_row, matching_row, newer_row])
    app = _make_runtime_test_app(fake_db, monkeypatch)

    with TestClient(app) as client:
        resp = client.get(
            '/api/v1/ai-native/audit',
            params={
                'created_at_from': '2026-05-20T00:00:00Z',
                'created_at_to': '2026-05-20T23:59:59Z',
            },
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['total'] == 1
    assert data['items'][0]['trace_id'] == 'trace-in-range'
