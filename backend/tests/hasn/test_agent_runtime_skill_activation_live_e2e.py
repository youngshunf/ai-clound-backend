"""云端 Hermes required 技能活体 E2E：真实 PG、Agent 鉴权、Runtime 与模型。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_agent_runtime import router as runtime_router
from backend.app.hasn.model import HasnAgentMcpKeys, HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_agent_mcp_keys import IssueAgentMcpKeyParam
from backend.app.hasn.service.hasn_agent_mcp_keys_service import hasn_agent_mcp_keys_service
from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    hasn_agent_runtime_dispatch_service,
)
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeClient, HermesRuntimeError
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.common.exception.errors import BaseExceptionError
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

OWNER_USER_ID = int(os.environ.get('HASN_SKILL_E2E_OWNER_USER_ID', '4'))
PROFILE_ID = os.environ.get('HASN_SKILL_E2E_PROFILE_ID', '100001-content_operator')
PACKAGE_ID = 'huanxing/creator-playbook'
PACKAGE_VERSION = '1.0.0'
PACKAGE_HASH = 'sha256:4cf323f77cb19a92aab60ac960f787835e52f0a022a453cb5b9a3d6e8191bdbc'
PACKAGE_SLUG = 'creator-playbook'
MEMBER_IDS = [
    'huanxing/official/creator-playbook',
    'huanxing/official/hasn-mcp-tools',
    'huanxing/official/task-management',
    'huanxing/search/competitor-analysis',
    'huanxing/search/newsnow',
    'huanxing/social/social-media-content',
]
REQUIREMENTS_HASH = '98a29de25ca6f3fdfedabdf3b0ba4c6b3282752e21eb471d97905647b1d00b22'
MODEL_MARKER = 'TASK12-CLOUD-HERMES-OK'
MODEL_ID = os.environ.get('HASN_SKILL_E2E_MODEL', 'agnes-2.5-flash')

_APP = FastAPI()
_APP.include_router(runtime_router, prefix='/api/v1/hasn/agent/runtime')


@_APP.exception_handler(BaseExceptionError)
def _error_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.code,
        content={'code': exc.code, 'msg': str(exc.msg), 'data': None},
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if value:
        return value
    if os.environ.get('HASN_SKILL_E2E_LIVE_REQUIRED') == '1':
        pytest.fail(f'缺少云端活体环境变量 {name}')
    pytest.skip(f'缺少云端活体环境变量 {name}')


def _requirements() -> dict[str, Any]:
    return {
        'skills': [],
        'bundles': [
            {
                'package_id': PACKAGE_ID,
                'version': PACKAGE_VERSION,
                'content_hash': PACKAGE_HASH,
                'bundle_slug': PACKAGE_SLUG,
                'member_skill_ids': MEMBER_IDS,
                'activation_mode': 'guided',
            }
        ],
    }


def _directory_fingerprints(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [
        {
            'path': path.relative_to(root).as_posix(),
            'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob('*'))
        if path.is_file()
    ]


def _parse_sse(body: str) -> tuple[list[str], str]:
    event_types: list[str] = []
    output: list[str] = []
    for line in body.splitlines():
        if not line.startswith('data:'):
            continue
        raw = line.removeprefix('data:').strip()
        if not raw or raw == '[DONE]':
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get('type') or event.get('event') or event.get('status') or 'unknown')
        event_types.append(event_type)
        for key in ('delta', 'text', 'content', 'message'):
            value = event.get(key)
            if isinstance(value, str):
                output.append(value)
    return event_types, ''.join(output)


def _compact_events(event_types: list[str]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for event_type in event_types:
        if compacted and compacted[-1]['type'] == event_type:
            compacted[-1]['count'] += 1
        else:
            compacted.append({'type': event_type, 'count': 1})
    return compacted


def _write_evidence(
    path: Path,
    evidence: dict[str, Any],
    *,
    secrets: list[str],
) -> None:
    payload = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
    for secret in secrets:
        assert not secret or secret not in payload, '云端活体证据不得包含凭据'
    for marker in ('X-Amz-Signature', 'X-Amz-Credential', 'hasn_amk_', 'eyJ'):
        assert marker not in payload, '云端活体证据不得包含签名 URL 或凭据'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(payload + '\n', encoding='utf-8')
    temporary.replace(path)


def _live_paths_and_llm_key() -> tuple[Path, Path, Path, str]:
    """同步读取活体路径和已有真实凭据，避免在异步 fixture 中阻塞事件循环。"""
    profile_root = Path(_required_env('HASN_SKILL_E2E_PROFILE_ROOT')).resolve()
    shared_skills_root = Path(_required_env('HASN_SKILL_E2E_SHARED_SKILLS_ROOT')).resolve()
    evidence_path = Path(_required_env('HASN_SKILL_E2E_EVIDENCE_PATH')).resolve()
    llm_source_root = Path(
        os.environ.get('HASN_SKILL_E2E_LLM_SOURCE_PROFILE_ROOT', str(profile_root))
    ).resolve()
    secrets_path = llm_source_root / 'secrets.json'
    if not secrets_path.is_file():
        pytest.fail(f'云端活体验收档案缺少凭据文件: {secrets_path}')
    profile_secrets = json.loads(secrets_path.read_text(encoding='utf-8'))
    llm_api_key = str(profile_secrets.get('llm_api_key') or '')
    if not llm_api_key:
        pytest.fail('云端活体验收档案缺少真实 LLM 凭据')
    return profile_root, shared_skills_root, evidence_path, llm_api_key


@pytest_asyncio.fixture
async def live_cloud_runtime() -> Any:
    runtime_url = _required_env('HASN_SKILL_E2E_RUNTIME_URL')
    runtime_token = _required_env('HASN_SKILL_E2E_RUNTIME_TOKEN')
    profile_root, shared_skills_root, evidence_path, llm_api_key = _live_paths_and_llm_key()

    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    original_client = hasn_agent_runtime_dispatch_service.runtime_client
    runtime_client = HermesRuntimeClient(
        base_url=runtime_url,
        api_token=runtime_token,
        timeout_seconds=300,
    )
    original_llm_payload: dict[str, Any] | None = None
    original_mapping_token: str | None = None
    switched_to_platform = False
    profile_existed = True
    agent_id: str | None = None
    try:
        identity_suffix = uuid.uuid4().hex[:8]
        agent_id = f'a_task12_cloud_{identity_suffix}'
        owner = (
            await session.execute(select(HasnHumans).where(HasnHumans.user_id == OWNER_USER_ID).limit(1))
        ).scalar_one_or_none()
        if owner is None:
            pytest.fail(f'真实 PostgreSQL 缺少活体验收用户 {OWNER_USER_ID}')
        owner_id = owner.hasn_id
        mapping = (
            await session.execute(
                select(LlmNewapiUserMapping).where(LlmNewapiUserMapping.huanxing_user_id == OWNER_USER_ID).limit(1)
            )
        ).scalar_one_or_none()
        if mapping is None:
            pytest.fail(f'真实 PostgreSQL 缺少活体验收用户 {OWNER_USER_ID} 的 NewAPI 映射')
        # 云端 provision 必须经过真实 owner 凭据解析。跨进程 Runtime 会立刻回调云端权威 API，
        # 因此测试 Agent 与凭据必须先真实提交，最后再按主键精确删除并恢复映射。
        original_mapping_token = mapping.newapi_token_key
        mapping.newapi_token_key = llm_api_key.removeprefix('sk-')
        session.add(
            HasnAgents(
                hasn_id=agent_id,
                star_id=f'task12a{identity_suffix}',
                owner_id=owner_id,
                display_name='Task12云端内容运营',
                agent_name='content_operator',
                type='cloud',
                runtime_location='cloud',
                role='specialist',
                api_key_hash='task12-live-e2e',
                runtime_config_json={'models': {'main': MODEL_ID}},
                status='active',
                created_via='client',
                profile_revision=1,
            )
        )
        await session.commit()
        issued = await hasn_agent_mcp_keys_service.issue(
            session,
            obj=IssueAgentMcpKeyParam(agent_hasn_id=agent_id, scopes=[], node_id=None),
            owner_hasn_id=owner_id,
            owner_user_id=OWNER_USER_ID,
        )
        await session.commit()

        def _yield_session() -> Iterator[Any]:
            yield session

        _APP.dependency_overrides[get_db] = _yield_session
        _APP.dependency_overrides[get_db_transaction] = _yield_session
        hasn_agent_runtime_dispatch_service.runtime_client = runtime_client

        try:
            llm_status = await runtime_client._request(
                'GET',
                f'/runtime/v1/profiles/{PROFILE_ID}/llm/status',
            )
        except HermesRuntimeError as error:
            if error.status_code != 404:
                raise
            profile_existed = False
        else:
            current_llm = llm_status.get('config') if isinstance(llm_status, dict) else None
            if not isinstance(current_llm, dict):
                pytest.fail(f'云端活体验收档案 {PROFILE_ID} 缺少 LLM 配置')
            original_llm_payload = {
                'mode': current_llm['mode'],
                'provider': current_llm['provider'],
                'base_url': current_llm['base_url'],
                'api_key': llm_api_key,
                'model': current_llm['model'],
                'fallback_models': current_llm.get('fallback_models', []),
            }
            if current_llm.get('plan_id'):
                original_llm_payload['plan_id'] = current_llm['plan_id']
            platform_payload = {**original_llm_payload, 'mode': 'platform'}
            await runtime_client._request(
                'PUT',
                f'/runtime/v1/profiles/{PROFILE_ID}/llm',
                json=platform_payload,
            )
            switched_to_platform = True

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_APP),
            base_url='http://cloud-live-e2e',
            timeout=600,
        ) as client:
            yield SimpleNamespace(
                client=client,
                authorization={'Authorization': f'Bearer {issued.key}'},
                runtime_token=runtime_token,
                agent_key=issued.key,
                agent_id=agent_id,
                owner_id=owner_id,
                profile_root=profile_root,
                shared_skills_root=shared_skills_root,
                evidence_path=evidence_path,
            )
    finally:
        try:
            if switched_to_platform and original_llm_payload is not None:
                await runtime_client._request(
                    'PUT',
                    f'/runtime/v1/profiles/{PROFILE_ID}/llm',
                    json=original_llm_payload,
                )
            if not profile_existed:
                for resource in ('credential', ''):
                    try:
                        await runtime_client._request(
                            'DELETE',
                            f'/runtime/v1/profiles/{PROFILE_ID}/{resource}'.rstrip('/'),
                        )
                    except HermesRuntimeError as error:
                        if error.status_code != 404:
                            raise
        finally:
            hasn_agent_runtime_dispatch_service.runtime_client = original_client
            _APP.dependency_overrides.clear()
            try:
                await session.rollback()
                if agent_id is not None:
                    await session.execute(
                        sa.delete(HasnAgentMcpKeys).where(HasnAgentMcpKeys.agent_hasn_id == agent_id)
                    )
                    await session.execute(sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id))
                if original_mapping_token is not None:
                    mapping = (
                        await session.execute(
                            select(LlmNewapiUserMapping)
                            .where(LlmNewapiUserMapping.huanxing_user_id == OWNER_USER_ID)
                            .limit(1)
                        )
                    ).scalar_one()
                    mapping.newapi_token_key = original_mapping_token
                await session.commit()
            finally:
                await session.close()
                await engine.dispose()


async def test_cloud_runtime_required_skill_live_e2e(
    live_cloud_runtime: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """无 daemon/设备参与时，云端同侧完成 ensure、激活并运行真实模型。"""
    live = live_cloud_runtime
    requirements = _requirements()
    global_bundle_root = Path.home() / '.hermes' / 'skill-bundles'
    global_before = _directory_fingerprints(global_bundle_root)
    trace_id = f'task12-cloud-{uuid.uuid4().hex}'

    ensure_response = await live.client.post(
        '/api/v1/hasn/agent/runtime/skills/ensure',
        headers=live.authorization,
        json={
            'runtime_profile_id': PROFILE_ID,
            'requirements_hash': REQUIREMENTS_HASH,
            'requirements': requirements,
            'trace_id': trace_id,
        },
    )
    assert ensure_response.status_code == 200, ensure_response.text
    receipt = ensure_response.json()['data']
    assert receipt['status'] == 'success'
    assert receipt['requirements_hash'] == REQUIREMENTS_HASH
    assert not receipt['missing']
    assert {item['skill_id'] for item in receipt['skills']} == set(MEMBER_IDS)
    assert all(item['materialized'] and item['discoverable'] for item in receipt['skills'])

    run_response = await live.client.post(
        '/api/v1/hasn/agent/runtime/runs',
        headers=live.authorization,
        json={
            'runtime_profile_id': PROFILE_ID,
            'requirements_hash': REQUIREMENTS_HASH,
            'requirements': requirements,
            'trace_id': trace_id,
            'payload': {
                'input': [
                    {
                        'role': 'user',
                        'content': (
                            '这是云端技能激活活体验收。只允许且必须调用一次 skill_view，'
                            '参数中的技能名必须精确为 creator-playbook；不要读取包内其他'
                            '技能。该调用成功后立即停止工具调用，只回复 '
                            'TASK12-CLOUD-HERMES-OK creator-playbook。'
                        ),
                    }
                ],
                'dispatch_id': trace_id,
                'conversation_id': trace_id,
                'hasn_id': live.agent_id,
                'tool_execution': 'enabled',
            },
        },
    )
    assert run_response.status_code == 200
    assert 'event: error' not in run_response.text, run_response.text
    event_types, model_output = _parse_sse(run_response.text)
    assert 'run.completed' in event_types
    assert any(event_type.startswith('tool.') for event_type in event_types)
    assert MODEL_MARKER in model_output

    bundle_path = live.profile_root / 'skill-bundles' / f'{PACKAGE_SLUG}.yaml'
    assert bundle_path.is_file()
    runtime_files = [
        {
            'path': f'skill-bundles/{bundle_path.name}',
            'bytes': bundle_path.stat().st_size,
            'sha256': hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        }
    ]
    receipt_skills = {
        item['skill_id']: item
        for item in receipt['skills']
        if isinstance(item, dict)
    }
    for skill_id in MEMBER_IDS:
        slug = skill_id.rsplit('/', 1)[-1]
        location_kind = receipt_skills[skill_id].get('location_kind')
        if location_kind == 'profile':
            skill_root = live.profile_root / 'skills' / slug
        elif location_kind == 'shared':
            skill_root = live.shared_skills_root / slug
        else:
            pytest.fail(f'云端 Runtime 返回不支持的技能物化域: {skill_id}={location_kind}')
        files = _directory_fingerprints(skill_root)
        assert files, f'云端 Runtime 缺少技能包成员 {skill_id}'
        runtime_files.extend(
            {
                **item,
                'path': f'{location_kind}/skills/{slug}/{item["path"]}',
            }
            for item in files
        )

    assert global_before == _directory_fingerprints(global_bundle_root)
    log_text = caplog.text
    assert live.runtime_token not in log_text
    assert live.agent_key not in log_text
    assert 'X-Amz-Signature' not in log_text
    assert 'REQUIRED SKILL CONTEXT BEGIN' not in log_text

    _write_evidence(
        live.evidence_path,
        {
            'feature': 'runtime_skill_activation_cloud_hermes',
            'status': 'passed',
            'device_online_required': False,
            'auth': 'agent_mcp_key',
            'agent_id': live.agent_id,
            'profile_id': PROFILE_ID,
            'model': MODEL_ID,
            'requirements_hash': REQUIREMENTS_HASH,
            'receipt': {
                'receipt_id': receipt['receipt_id'],
                'profile_ref': receipt['profile_ref'],
                'index_generation': receipt['index_generation'],
                'skills': sorted(item['skill_id'] for item in receipt['skills']),
                'bundles': receipt['bundles'],
            },
            'phases': [
                {'phase': 'requested', 'status': 'success'},
                {'phase': 'materialized', 'status': 'success'},
                {
                    'phase': 'indexed',
                    'status': 'success',
                    'generation': receipt['index_generation'],
                },
                {'phase': 'activated', 'status': 'success'},
                {'phase': 'run_created', 'status': 'success'},
            ],
            'run': {
                'dispatch_id': trace_id,
                'events': _compact_events(event_types),
                'tool_event_count': sum(event_type.startswith('tool.') for event_type in event_types),
                'model_output_sha256': hashlib.sha256(model_output.encode('utf-8')).hexdigest(),
                'marker_detected': True,
            },
            'runtime_files': runtime_files,
            'global_bundle_writes': {
                'unchanged': True,
                'before_file_count': len(global_before),
            },
            'log_redaction': {'passed': True},
        },
        secrets=[live.runtime_token, live.agent_key],
    )
