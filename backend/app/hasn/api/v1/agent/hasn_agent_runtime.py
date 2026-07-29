"""云端 Runtime 派发代理 - Agent 端 API（双形态 Runtime，设计 08/02 §5 + 实施 01 阶段 B）。

认证方式: DependsAgentJwtAuth（Agent JWT / Agent MCP Key）。身份恒取自已验证凭证，
绝不从请求体读取身份字段。仅 runtime_location=cloud 的分身允许走此面；local 分身经本地
sidecar 派发，调用此面会被拒（403）。

数据面（POST /runs 的 SSE 中继）走 daemon →（BackendGateway agent-scoped, Agent JWT）→
本面 → hermes-runtime(127.0.0.1) → 上游 gateway（Docker 沙箱）。详见
hasn_agent_runtime_dispatch_service。
"""

import json

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.hasn.schema.hasn_agents import (
    RuntimeHealthResponse,
    RuntimeRunCancelRequest,
    RuntimeRunCancelResponse,
    RuntimeRunRequest,
    RuntimeSkillEnsureRequest,
    RuntimeSkillEnsureResponse,
    RuntimeSkillMutateRequest,
    RuntimeSkillMutateResponse,
    RuntimeSkillReadResponse,
    RuntimeSkillsListResponse,
)
from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    hasn_agent_runtime_dispatch_service,
)
from backend.app.hasn.service.hasn_agent_runtime_provision_service import (
    ensure_cloud_profile_provisioned,
)
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeError
from backend.common.dataclasses import AgentTokenPayload
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


def _runtime_error_response(exc: HermesRuntimeError) -> JSONResponse:
    status_code = exc.status_code or 503
    return JSONResponse(
        status_code=status_code,
        content={
            'code': status_code,
            'msg': exc.error,
            'data': exc.to_response_data(),
        },
    )


async def _runtime_error_stream(exc: HermesRuntimeError) -> AsyncIterator[bytes]:  # ruff: ignore[unused-async]
    payload: dict[str, Any] = {'error': exc.error}
    if exc.details:
        payload['details'] = exc.details
    if exc.status_code is not None:
        payload['status_code'] = exc.status_code
    yield f'event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'.encode()


@router.post(
    '/runs',
    summary='云端分身派发一个 run（SSE 流式回帧）',
)
async def create_runtime_run(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    body: RuntimeRunRequest,
) -> StreamingResponse:
    # 先用请求级 db 完成归属校验 + 云端形态闸门（StreamingResponse 返回后 db 会关闭，
    # 不得在生成器内再触碰 db）。
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=body.runtime_profile_id,
    )
    # provision、required ensure 与 activate 都必须在创建上游 run 前完成。任一步失败都直接
    # 返回 SSE error，禁止进入 relay 触发 gateway/start 或 POST /v1/runs。
    try:
        await ensure_cloud_profile_provisioned(
            db,
            agent_hasn_id=agent.agent_hasn_id,
            owner_hasn_id=agent.owner_hasn_id,
            profile_id=profile_id,
            runtime_client=hasn_agent_runtime_dispatch_service.runtime_client,
            trace_id=body.trace_id,
        )
        payload = await hasn_agent_runtime_dispatch_service.prepare_cloud_run_payload(
            runtime_profile_id=profile_id,
            payload=body.payload,
            requirements=body.requirements,
            requirements_hash=body.requirements_hash,
            trace_id=body.trace_id,
        )
    except HermesRuntimeError as exc:
        log.warning(f'cloud runtime dispatch preflight failed for {profile_id}: {exc.error}')
        generator = _runtime_error_stream(exc)
    else:
        generator = hasn_agent_runtime_dispatch_service.relay_run_stream(
            runtime_profile_id=profile_id,
            payload=payload,
            trace_id=body.trace_id,
        )
    # SSE over nginx 加固：`X-Accel-Buffering: no` 关闭 nginx 对该响应的缓冲（帧立即下行、
    # 不被 proxy_buffering 攒整段）；配合 relay_run_stream 内置心跳，避免首帧前静默期
    # （沙箱冷启动 + provision + LLM 首 token 易 >60s）撞 nginx proxy_read_timeout 切断长连接
    # （症状：daemon 侧 `cloud runtime relay SSE read failed: error decoding response body`）。
    return StreamingResponse(
        generator,
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.post(
    '/skills/ensure',
    summary='在云端 dispatch 同侧准备 required 技能',
    response_model=None,
)
async def ensure_runtime_skills(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    body: RuntimeSkillEnsureRequest,
) -> ResponseSchemaModel[RuntimeSkillEnsureResponse] | JSONResponse:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=body.runtime_profile_id,
    )
    try:
        await ensure_cloud_profile_provisioned(
            db,
            agent_hasn_id=agent.agent_hasn_id,
            owner_hasn_id=agent.owner_hasn_id,
            profile_id=profile_id,
            runtime_client=hasn_agent_runtime_dispatch_service.runtime_client,
            trace_id=body.trace_id,
        )
        result = await hasn_agent_runtime_dispatch_service.ensure_cloud_skill_requirements(
            runtime_profile_id=profile_id,
            requirements=body.requirements,
            requirements_hash=body.requirements_hash,
            trace_id=body.trace_id,
        )
    except HermesRuntimeError as exc:
        log.warning(f'cloud runtime skill ensure failed for {profile_id}: {exc.error}')
        return _runtime_error_response(exc)
    return response_base.success(data=RuntimeSkillEnsureResponse.model_validate(result))


@router.get(
    '/health',
    summary='云端分身运行时健康（供 daemon 判可达性，双形态 Runtime 设计 08/02 §81）',
)
async def get_cloud_runtime_health(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    runtime_profile_id: str,
) -> ResponseSchemaModel[RuntimeHealthResponse]:
    # 复用 resolve_cloud_profile 的归属校验 + 云端形态闸门：身份恒取自 Agent JWT，
    # 仅 runtime_location=cloud 分身可查；local 分身/越权 → 403。
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=runtime_profile_id,
    )
    result = await hasn_agent_runtime_dispatch_service.cloud_runtime_health(runtime_profile_id=profile_id)
    return response_base.success(data=RuntimeHealthResponse(**result))


@router.post(
    '/runs/{run_id}/cancel',
    summary='取消云端分身进行中的 run',
)
async def cancel_runtime_run(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    run_id: str,
    body: RuntimeRunCancelRequest,
) -> ResponseSchemaModel[RuntimeRunCancelResponse]:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=body.runtime_profile_id,
    )
    try:
        result = await hasn_agent_runtime_dispatch_service.cancel_run(
            runtime_profile_id=profile_id, run_id=run_id, trace_id=body.trace_id
        )
    except HermesRuntimeError:
        # 上游不可达/取消失败：如实返回 cancelled=false（零 fake，不谎报已取消）。
        return response_base.success(data=RuntimeRunCancelResponse(run_id=run_id, cancelled=False))
    return response_base.success(data=RuntimeRunCancelResponse(run_id=result['run_id'], cancelled=result['cancelled']))


# ---------------------------------------------------------------------------
# 运行时技能读/管理（双形态 Runtime，设计 04：云端 Runtime 收敛到 RuntimeAdapter）
#
# daemon 侧 Hermes 适配器按 runtime_location 分叉：local→本地 sidecar，cloud→经
# BackendGateway agent-scoped 通道（Agent JWT）打本组端点 → 云端 sidecar 控制面读。
# 归属校验 + 云端形态闸门统一复用 resolve_cloud_profile（身份恒取自 Agent JWT）。
# ---------------------------------------------------------------------------


@router.get(
    '/skills',
    summary='列出云端分身运行时技能（双形态 Runtime，设计 04）',
)
async def list_runtime_skills(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    runtime_profile_id: str,
) -> ResponseSchemaModel[RuntimeSkillsListResponse]:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=runtime_profile_id,
    )
    result = await hasn_agent_runtime_dispatch_service.list_skills(runtime_profile_id=profile_id)
    return response_base.success(data=RuntimeSkillsListResponse(skills=result.get('skills') or []))


@router.get(
    '/skills/{skill_id}',
    summary='读取云端分身某技能正文（SKILL.md）',
)
async def read_runtime_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    skill_id: str,
    runtime_profile_id: str,
) -> ResponseSchemaModel[RuntimeSkillReadResponse]:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=runtime_profile_id,
    )
    result = await hasn_agent_runtime_dispatch_service.read_skill(runtime_profile_id=profile_id, skill_id=skill_id)
    # 显式取字段（sidecar 返回含 profile_id 等额外键，逐字段构造避免透传噪音）。
    return response_base.success(
        data=RuntimeSkillReadResponse(
            skill_id=str(result.get('skill_id') or skill_id),
            name=str(result.get('name') or ''),
            description=str(result.get('description') or ''),
            content=str(result.get('content') or ''),
            enabled=bool(result.get('enabled', False)),
        )
    )


@router.post(
    '/skills/{skill_id}/enable',
    summary='启用云端分身某技能',
)
async def enable_runtime_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    skill_id: str,
    body: RuntimeSkillMutateRequest,
) -> ResponseSchemaModel[RuntimeSkillMutateResponse]:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=body.runtime_profile_id,
    )
    result = await hasn_agent_runtime_dispatch_service.enable_skill(
        runtime_profile_id=profile_id, skill_id=skill_id, trace_id=body.trace_id
    )
    return response_base.success(data=RuntimeSkillMutateResponse(**result))


@router.post(
    '/skills/{skill_id}/disable',
    summary='停用云端分身某技能',
)
async def disable_runtime_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    skill_id: str,
    body: RuntimeSkillMutateRequest,
) -> ResponseSchemaModel[RuntimeSkillMutateResponse]:
    profile_id = await hasn_agent_runtime_dispatch_service.resolve_cloud_profile(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        runtime_profile_id=body.runtime_profile_id,
    )
    result = await hasn_agent_runtime_dispatch_service.disable_skill(
        runtime_profile_id=profile_id, skill_id=skill_id, trace_id=body.trace_id
    )
    return response_base.success(data=RuntimeSkillMutateResponse(**result))
