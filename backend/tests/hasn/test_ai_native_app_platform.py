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
    assert manifest['version'] == '2.3.0'
    assert manifest['workspace_scope'] == ['personal', 'enterprise']
    assert manifest['collaboration_mode'] == 'workspace_shared'
    assert manifest['capabilities'][0]['tool_id'] == 'knowledge.search'
    assert manifest['capabilities'][0]['mcp_name'] == 'hasn.knowledge.search'
    assert manifest['capabilities'][0]['required_scopes'] == ['knowledge:read']
    # AI-Native 重做（《知识库AI-Native应用重设计（RAGFlow处理后端）》§2.4）：工具回归
    # manifest App 工具（transport=gateway_internal，handler 落 knowledge service），
    # 本地与云端 Runtime 同一通路；`commit_document` 退役（上传即自动解析，D6）。
    # 2.1.0：补齐 kb 生命周期 + 文档 列/删；2.2.0：update_kb / move_document；
    # 2.3.0：原生文档 5000 字上限 + 文档深链保存时强校验 + 新增只读预检工具 check_links。
    tool_ids = [t['tool_id'] for t in manifest['tools']]
    assert tool_ids == [
        'knowledge.search',
        'knowledge.list_datasets',
        'knowledge.create_kb',
        'knowledge.update_kb',
        'knowledge.delete_kb',
        'knowledge.fetch_doc',
        'knowledge.list_documents',
        'knowledge.upload_document',
        'knowledge.delete_document',
        'knowledge.write_doc',
        'knowledge.check_links',
        'knowledge.move_document',
        'knowledge.list_folders',
        'knowledge.create_folder',
        'knowledge.update_folder',
        'knowledge.delete_folder',
    ]
    assert all(t['transport'] == 'gateway_internal' for t in manifest['tools'])
    assert all(t['handler'] == t['tool_id'] for t in manifest['tools'])
    assert 'knowledge.commit_document' not in tool_ids


def test_manifest_validator_accepts_builtin_knowledge_manifest() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.app_catalog_registry import app_catalog_registry

    registry = AINativeAppRegistry(catalog_registry=app_catalog_registry)

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
    from backend.app.hasn.service.app_catalog_registry import App, AppCatalogRegistry

    workbench = AppCatalogRegistry()
    workbench.register(
        App(
            id='knowledge',
            name='知识库',
            icon='book-open',
            description='个人知识库',
            scope=('personal',),
            collaboration_mode='none',
            entry_route='/apps/knowledge',
            install_policy='auto',
        )
    )
    registry = AINativeAppRegistry(catalog_registry=workbench)

    result = registry.validate_manifest(KNOWLEDGE_AI_NATIVE_MANIFEST)

    assert result.valid is False
    assert 'workspace_scope_exceeds_workbench_scope' in result.errors
    assert 'collaboration_mode_mismatch' in result.errors


def test_hasn_router_mounts_ai_native_app_routes() -> None:
    from backend.app.hasn.api.router import ai_native

    routes = {getattr(route, 'path', '') for route in ai_native.routes}

    assert '/api/v1/ai-native/apps' in routes
    assert '/api/v1/ai-native/apps/{app_id}' in routes
    assert '/api/v1/ai-native/apps/{app_id}/validate' in routes
    assert '/api/v1/ai-native/apps/{app_id}/publish' in routes


@pytest.mark.asyncio
async def test_publish_builtin_manifest_uses_published_status() -> None:
    from backend.app.hasn.service.ai_native_app_registry import AINativeAppRegistry
    from backend.app.hasn.service.ai_native_builtin_manifests import KNOWLEDGE_AI_NATIVE_MANIFEST
    from backend.app.hasn.service.app_catalog_registry import app_catalog_registry

    registry = AINativeAppRegistry(catalog_registry=app_catalog_registry)
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


class _WorkspaceAppStub:
    """应用平台 v3 P3：hasn_workspace_app 已退役（挂载概念废除），gateway 不再查该表。
    历史用例仍构造「挂载行」入参，此处保留轻量 stub 占位——_FakeDb 已不再消费它。
    """

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


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
            if 'owner_hasn_id_1' in params:
                rows = [row for row in rows if row.owner_hasn_id == params['owner_hasn_id_1']]
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

    async def commit(self) -> None:
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


def _community_manifest_payload(
    *,
    collaboration_mode: str | None = None,
    tool_id_for_roles: str | None = None,
    workspace_roles: list[str] | None = None,
) -> dict[str, Any]:
    from backend.app.hasn.service.ai_native_app_registry import _manifest_hash
    from backend.app.hasn.service.ai_native_builtin_manifests import COMMUNITY_AI_NATIVE_MANIFEST
    from backend.utils.timezone import timezone

    manifest = deepcopy(COMMUNITY_AI_NATIVE_MANIFEST)
    if collaboration_mode is not None:
        manifest['collaboration_mode'] = collaboration_mode
    if tool_id_for_roles is not None and workspace_roles is not None:
        for cap in manifest['capabilities']:
            if cap.get('tool_id') == tool_id_for_roles:
                cap['workspace_roles'] = workspace_roles
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

    # 审计 GET 端点已改为 owner-scope（DependsJwtAuth + 服务端按 owner_hasn_id 硬过滤）：
    # 测试 app 放行 owner 鉴权并把登录人固定解析为 _FakeAgent.owner_hasn_id，让 owner
    # 过滤在用例里真实生效（rows 的 owner_hasn_id 决定可见性，他人 owner 行被隔离）。
    async def fake_owner_auth() -> None:
        return None

    async def fake_require_owner(_db: Any, _request: Any) -> str:
        return _FakeAgent.owner_hasn_id

    app.dependency_overrides[module.DependsJwtAuth.dependency] = fake_owner_auth
    monkeypatch.setattr(module, '_require_owner_hasn_id', fake_require_owner)
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
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db: Any = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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


# 应用平台 v3 P3（设计 17 决策①）：test_runtime_capabilities_filters_disabled_workspace_app 已删除——
# 「企业 override 显式 disabled 从发现里隐藏」一档随 hasn_workspace_app 退役不复存在
# （published 即可发现，付费墙在 invoke 时把关）。


def test_enterprise_runtime_capabilities_filter_by_workspace_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=_WorkspaceAppStub(
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
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=_WorkspaceAppStub(
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


def test_runtime_tool_call_ask_mode_returns_approval_required(monkeypatch: pytest.MonkeyPatch) -> None:
    # 令牌重试模型（doc15 §3.1）：ask 命中 → 中继路径回完整 approval_required(MCP_9215) 信封 +
    # request_id，hasn-mcp 据此挂起 ApprovalBroker / 换票重试。绝不当次放行。
    import backend.app.mcp.ask_gate as ask_gate_module
    import backend.common.security.agent_jwt as agent_jwt_module

    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _community_manifest_payload()

    async def _ask(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'community:read': 'ask'},
            'scopes': [],
            'post_needs_review': False,
        }

    open_calls: dict[str, Any] = {}

    async def _open_request(**kwargs: Any) -> dict[str, Any]:
        open_calls['tool_name'] = kwargs.get('tool_name')  # 记录挂起请求（不当次放行）
        return {
            'ok': False,
            'error': 'approval_required',
            'code': 'MCP_9215',
            'message': f'需要主人批准后才能执行：{kwargs.get("tool_name")}',
            'approval': {
                'request_id': 'areq_stub',
                'tool_name': kwargs.get('tool_name'),
                'args_digest': {},
                'expires_in': 600,
            },
        }

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _ask)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _open_request)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={'workspace': None, 'input': {'post_id': 'post_01J'}, 'trace_id': 'trace-ask-ok'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    # 令牌重试模型：中继面 ask → 回 approval_required(MCP_9215) + request_id（主人批准后带票重试）。
    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'approval_required'
    assert data['error']['code'] == 'MCP_9215'
    assert data['approval']['request_id'] == 'areq_stub'  # 中继据此挂起 / 换票
    assert open_calls['tool_name'] == 'hasn.community.get_post'  # 确实记录了 ask 审批请求
    assert fake_db.added[-1].decision == 'approval_required'


def test_runtime_tool_call_ask_mode_audits_approval_required(monkeypatch: pytest.MonkeyPatch) -> None:
    # 令牌重试模型：ask → 记录审批请求 + approval_required(MCP_9215) 审计，不触达 handler（带票重试）。
    import backend.app.mcp.ask_gate as ask_gate_module
    import backend.common.security.agent_jwt as agent_jwt_module

    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _community_manifest_payload()

    async def _ask(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'community:read': 'ask'},
            'scopes': [],
            'post_needs_review': False,
        }

    async def _open_request(**kwargs: Any) -> dict[str, Any]:
        return {
            'ok': False,
            'error': 'approval_required',
            'code': 'MCP_9215',
            'message': f'需要主人批准后才能执行：{kwargs.get("tool_name")}',
            'approval': {'request_id': 'areq_stub', 'tool_name': kwargs.get('tool_name')},
        }

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _ask)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _open_request)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={'workspace': None, 'input': {'post_id': 'post_01J'}, 'trace_id': 'trace-ask-no'},
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'approval_required'
    assert data['error']['code'] == 'MCP_9215'
    assert data['approval']['request_id'] == 'areq_stub'
    assert fake_db.added[-1].error_code == 'MCP_9215'
    assert fake_db.added[-1].decision == 'approval_required'


def test_runtime_tool_call_ask_mode_with_valid_ticket_bypasses_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    # 令牌重试模型（doc15 §3.1）：批准后 hasn-mcp 带有效 X-Capability-Ticket 重发同一 ask 调用，
    # 中继网关验票跳闸 → 直接执行（decision=allow），不再开审批 / 不返回 approval_required。
    import backend.app.mcp.ask_gate as ask_gate_module
    import backend.common.security.agent_jwt as agent_jwt_module
    import backend.common.security.capability_ticket as ticket_module

    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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

    async def fake_manifest(_db: Any, _app_id: str) -> dict[str, Any]:
        return _community_manifest_payload()

    async def fake_get_agent_post(_db: Any, *, agent: Any, post_id: str) -> dict[str, Any]:
        return {
            'resource': {
                'type': 'community.post',
                'id': post_id,
                'app_id': 'community',
                'uri': f'hasn://app/community/posts/{post_id}',
            },
            'content': 'x',
        }

    async def _ask(_agent_hasn_id: str, _db: Any) -> dict[str, Any]:
        return {
            'default_mode': 'allow',
            'capability_modes': {'community:read': 'ask'},
            'scopes': [],
            'post_needs_review': False,
        }

    consume_calls: dict[str, Any] = {}

    async def _consume(ticket: str, *, agent_hasn_id: str, tool_name: str, args_hash: str) -> dict[str, Any] | None:
        consume_calls.update({'ticket': ticket, 'tool_name': tool_name, 'args_hash': args_hash})
        return {'request_id': 'areq_stub', 'typ': 'capability_ticket'}

    async def _open_request(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError('带有效票时绝不能再开审批（open_request 不应被调用）')

    marked: dict[str, Any] = {}

    async def _mark_consumed(request_id: str) -> None:
        marked['request_id'] = request_id

    monkeypatch.setattr(agent_jwt_module, 'get_agent_scopes_cached', _ask)
    monkeypatch.setattr(ticket_module, 'consume_capability_ticket', _consume)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'open_request', _open_request)
    monkeypatch.setattr(ask_gate_module.ask_approval_gate, 'mark_consumed', _mark_consumed)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)
    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)
    monkeypatch.setattr(gateway_module.community_service, 'get_agent_post_resource', fake_get_agent_post)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={'workspace': None, 'input': {'post_id': 'post_01J'}, 'trace_id': 'trace-ticket-ok'},
            headers={'Authorization': 'Bearer test-agent', 'X-Capability-Ticket': 'tkt_valid'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'allow'  # 验票跳闸 → 执行
    assert consume_calls['ticket'] == 'tkt_valid'
    assert consume_calls['tool_name'] == 'hasn.community.get_post'
    assert marked['request_id'] == 'areq_stub'  # 审批请求标记 consumed
    assert fake_db.added[-1].decision == 'allow'


def test_runtime_tool_call_community_get_post_returns_full_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    class CommunityAgent(_FakeAgent):
        scopes = ['community:read']

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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
    # scope 词表统一为冒号（community:read），manifest required_scopes 即冒号形态
    assert audit_row.required_scopes == ['community:read']


def test_runtime_tool_call_community_capability_deny_writes_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    # 三态模型（D1/D3）：owner 把 community:read 能力设 deny → community.get_article 被拒 15012。
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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


# 应用平台 v3 P3（设计 17 决策①）：test_runtime_tool_call_disabled_app_writes_audit 已删除——
# 15002「app_disabled_by_enterprise」拒绝档随 hasn_workspace_app 退役不复存在（published 即可调用，
# 准入仅由 entitlement 维度把关）。


def test_enterprise_runtime_tool_call_role_denial_writes_15004_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=_WorkspaceAppStub(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='community',
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
        return _community_manifest_payload(
            tool_id_for_roles='community.get_post', workspace_roles=['owner', 'admin']
        )

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'post_id': 'post_01J'},
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
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=_WorkspaceAppStub(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='community',
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
        return _community_manifest_payload(collaboration_mode='none')

    monkeypatch.setattr(gateway_module.workbench_domain_service, '_approved_membership', fake_membership)
    monkeypatch.setattr(gateway_module.ai_native_app_registry, 'ensure_builtin_published', fake_manifest)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'post_id': 'post_01J'},
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
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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

    monkeypatch.setattr(gateway_module.workbench_domain_service, 'get_active_workspace', fake_active_workspace)

    with TestClient(app) as client:
        resp = client.post(
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={
                'workspace': None,
                'input': {'post_id': ''},
                'trace_id': 'trace-invalid-input',
            },
            headers={'Authorization': 'Bearer test-agent'},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['decision'] == 'deny'
    # 入参绑定接缝（候选①）：按 capability.input_schema 校验后，15020 reason 由通用
    # 'input_schema_invalid' 升级为字段级 'input_invalid:<field>:<reason>'（更可调试）。
    # post_id 必填字符串给空串 → 'required'。
    assert data['error'] == {'code': '15020', 'message': 'input_invalid:post_id:required'}
    audit_row = fake_db.added[-1]
    assert audit_row.trace_id == 'trace-invalid-input'
    assert audit_row.decision == 'deny'
    assert audit_row.error_code == '15020'
    assert audit_row.context == {'reason': 'input_invalid:post_id:required'}


@pytest.mark.asyncio
async def test_runtime_gateway_revoked_agent_session_writes_15011_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.schema.ai_native_runtime import AiNativeToolCallRequest
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module
    from backend.common.security import agent_jwt as agent_jwt_module
    from backend.common.security.agent_jwt import jwt_encode_agent

    fake_db: Any = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
            workspace_kind='personal',
            user_id=12345,
            enterprise_id=None,
            app_id='community',
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
            'scopes': ['community.read'],
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

    revoked_request: Any = RequestWithRevokedToken()
    data = await gateway_module.ai_native_runtime_gateway.call_tool(
        fake_db,
        request=revoked_request,
        app_id='community',
        tool_id='community.get_post',
        body=AiNativeToolCallRequest(
            workspace=None,
            input={'post_id': 'post_01J'},
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

    fake_db = _FakeDb(
        workspace={'kind': 'personal', 'enterprise_id': None},
        app_row=_WorkspaceAppStub(
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
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={'workspace': None, 'input': {'post_id': 'post_01J'}, 'trace_id': 'trace-missing-agent-jwt'},
        )

    assert resp.status_code == 401, resp.text
    assert fake_db.added == []


def test_runtime_tool_call_inaccessible_workspace_writes_15003_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.hasn.service import ai_native_runtime_gateway as gateway_module

    fake_db = _FakeDb(
        workspace=None,
        app_row=_WorkspaceAppStub(
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=7,
            app_id='community',
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
            '/api/v1/ai-native/runtime/tools/community/community.get_post/call',
            json={
                'workspace': {'kind': 'enterprise', 'enterprise_id': 7},
                'input': {'post_id': 'post_01J'},
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


def test_runtime_audit_route_owner_scope_isolates_other_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    """安全：审计列表 owner-scope 硬过滤——即便客户端传他人分身的 agent_hasn_id，

    服务端按登录人 owner_hasn_id 过滤，只返回自己名下分身的审计，绝不泄露他人分身的工具调用。
    `_make_runtime_test_app` 把登录人固定解析为 _FakeAgent.owner_hasn_id（'h_001'）。
    """
    from backend.app.hasn.model import HasnAiNativeAppAudit

    mine = HasnAiNativeAppAudit(
        trace_id='trace-mine',
        step='local_tool',
        workspace_kind='personal',
        user_id=12345,
        enterprise_id=None,
        app_id='deck',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_001',
        owner_hasn_id='h_001',
        session_uuid='session-001',
        method='hasn.deck.create',
        capability_id=None,
        tool_id='deck.create',
        event_type='tool_call',
        required_scopes=['deck:write'],
        agent_scopes_snapshot=['deck:write'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref=None,
        error_code=None,
        context={},
    )
    mine.id = 50
    theirs = HasnAiNativeAppAudit(
        trace_id='trace-theirs',
        step='local_tool',
        workspace_kind='personal',
        user_id=99999,
        enterprise_id=None,
        app_id='deck',
        app_version='1.0.0',
        actor_type='agent',
        agent_hasn_id='a_999',
        owner_hasn_id='h_999',
        session_uuid='session-999',
        method='hasn.deck.create',
        capability_id=None,
        tool_id='deck.create',
        event_type='tool_call',
        required_scopes=['deck:write'],
        agent_scopes_snapshot=['deck:write'],
        workspace_role='owner',
        risk_level='low',
        decision='allow',
        confirmation_id=None,
        result_ref=None,
        error_code=None,
        context={},
    )
    theirs.id = 51
    fake_db = _FakeDb(workspace=None, audit_rows=[mine, theirs])
    app = _make_runtime_test_app(fake_db, monkeypatch)

    with TestClient(app) as client:
        # 故意传他人分身 ID：owner 过滤兜底，仍读不到他人审计。
        resp = client.get('/api/v1/ai-native/audit', params={'agent_hasn_id': 'a_999'})

    assert resp.status_code == 200, resp.text
    data = resp.json()['data']
    assert data['total'] == 0
    assert all(item['owner_hasn_id'] == 'h_001' for item in data['items'])
