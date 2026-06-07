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
    assert app.ui_kind == 'new_window'
    # window_url 指向加载外壳（loading shell），而非直接 ui/upload —— 避免冷启白屏（§6.1）。
    assert app.window_url == '/api/v1/apps/presentation/loading'
    assert app.window_origin == 'loopback'
    assert app.scope == ('personal',)
    assert app.collaboration_mode == 'none'
    assert app.install_policy == 'auto'

    manifest = app.to_manifest()
    # 三处端到端同步（§6.1）：新字段须随 to_manifest 透传给 daemon/WebUI。
    assert manifest['execution_mode'] == 'embedded_desktop'
    assert manifest['ui_kind'] == 'new_window'
    assert manifest['window_url'] == '/api/v1/apps/presentation/loading'
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


@pytest.mark.asyncio
async def test_agent_audit_report_persists_with_jwt_identity() -> None:
    """PRES-P4-d（D5）：分身经 Agent JWT POST /ai-native/audit/report 上报本地工具审计。

    真实 HTTP E2E（ASGITransport 走完整路由 + Agent JWT 值依赖 + 统一信封 + 真实 service/DAO）。
    安全断言（identity by auth）：落库行的 `agent_hasn_id`/`owner_hasn_id` 取自 **Agent JWT**、
    `actor_type` 强制 `agent`——ReportParam 结构上没有这些身份字段，body 无从冒名。
    DAO `create_model` 默认仅 `session.add()`，故用 capture-db 截获待落库行断言其字段（零 fake）。
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.app.hasn.api.v1 import ai_native_app as module
    from backend.app.hasn.model import HasnAiNativeAppAudit
    from backend.common.dataclasses import AgentTokenPayload
    from backend.database.db import get_db_transaction

    # ~38 字符，贴近真实 hasn_id 列宽（本测试用 capture-db 不落真库，长度仅为贴近真实）。
    agent_hasn_id = 'a_p4d_audit_agent_' + '0' * 20
    owner_hasn_id = 'h_p4d_audit_owner_' + '0' * 20

    captured: list[HasnAiNativeAppAudit] = []

    class _CaptureDb:
        """截获 CRUDPlus.create_model 的 session.add(instance)（默认不 flush/commit）。"""

        def add(self, instance: object) -> None:
            captured.append(instance)  # type: ignore[arg-type]

    async def _fake_db_tx():
        yield _CaptureDb()

    async def _fake_agent_auth() -> AgentTokenPayload:
        return AgentTokenPayload(
            agent_hasn_id=agent_hasn_id,
            agent_name='P4D 审计测试分身',
            owner_hasn_id=owner_hasn_id,
            owner_user_id=990011,
            scopes=['presentation:generate'],
            session_uuid='sess_p4d_audit',
            expire_time='2027-01-01T00:00:00+00:00',
        )

    app = FastAPI()
    app.include_router(module.audit_router, prefix='/api/v1/ai-native/audit')
    app.dependency_overrides[module.DependsAgentJwtAuth.dependency] = _fake_agent_auth
    app.dependency_overrides[get_db_transaction] = _fake_db_tx

    body = {
        'trace_id': 'tr_p4d_audit_1',
        'app_id': 'presentation',
        'method': 'hasn.presentation.generate',
        'decision': 'allow',
        'tool_id': 'hasn.presentation.generate',
        'required_scopes': ['presentation:generate'],
        'risk_level': 'write',
        'context': {'args_digest': 'hasn.presentation.generate｜n_slides=8'},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post('/api/v1/ai-native/audit/report', json=body)

    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env['data'] == {'trace_id': 'tr_p4d_audit_1', 'recorded': True}

    assert len(captured) == 1
    row = captured[0]
    assert isinstance(row, HasnAiNativeAppAudit)
    # 身份权威取自 JWT（非 body），actor_type 强制 agent。
    assert row.agent_hasn_id == agent_hasn_id
    assert row.owner_hasn_id == owner_hasn_id
    assert row.actor_type == 'agent'
    # body 字段如实透传。
    assert row.app_id == 'presentation'
    assert row.method == 'hasn.presentation.generate'
    assert row.tool_id == 'hasn.presentation.generate'
    assert row.decision == 'allow'
    assert row.required_scopes == ['presentation:generate']
    assert row.risk_level == 'write'
    # 本地工具默认步骤/工作空间。
    assert row.step == 'local_tool'
    assert row.workspace_kind == 'personal'


@pytest.mark.asyncio
async def test_agent_audit_report_requires_agent_jwt() -> None:
    """缺 Agent JWT → 401（端点受 DependsAgentJwtAuth 保护，不接受匿名上报）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.app.hasn.api.v1 import ai_native_app as module
    from backend.database.db import get_db_transaction

    class _CaptureDb:
        def add(self, instance: object) -> None:  # pragma: no cover - 不应被调用
            raise AssertionError('未鉴权不应落库')

    async def _fake_db_tx():
        yield _CaptureDb()

    app = FastAPI()
    app.include_router(module.audit_router, prefix='/api/v1/ai-native/audit')
    app.dependency_overrides[get_db_transaction] = _fake_db_tx  # 不 override 鉴权 → 真实 401

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        resp = await client.post(
            '/api/v1/ai-native/audit/report',
            json={'trace_id': 't', 'app_id': 'presentation', 'method': 'm', 'decision': 'allow'},
        )
    assert resp.status_code == 401, resp.text
