"""云端 Runtime 派发代理 - Agent 端 API（双形态 Runtime，设计 08/02 §5 + 实施 01 阶段 B）。

认证方式: DependsAgentJwtAuth（Agent JWT）。身份恒取自 JWT（agent_hasn_id/owner_hasn_id），
绝不从请求体读取身份字段。仅 runtime_location=cloud 的分身允许走此面；local 分身经本地
sidecar 派发，调用此面会被拒（403）。

数据面（POST /runs 的 SSE 中继）走 daemon →（BackendGateway agent-scoped, Agent JWT）→
本面 → hermes-runtime(127.0.0.1) → 上游 gateway（Docker 沙箱）。详见
hasn_agent_runtime_dispatch_service。
"""

from typing import Annotated

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.app.hasn.schema.hasn_agents import (
    RuntimeHealthResponse,
    RuntimeRunCancelRequest,
    RuntimeRunCancelResponse,
    RuntimeRunRequest,
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
    # provision-on-demand（双形态「provision 分叉」补缺）：云端分身首次/缺失时，由云端用 platform
    # LLM 把 profile 补 provision 到云端 hermes（覆盖存量分身 + 换/重启 hermes 自愈）。db 仍在请求级
    # 会话内（StreamingResponse 返回后才关）。best-effort：provision 失败不在此抛（保持 SSE 错误语义
    # 统一），让下面 relay 阶段如实报 profile_not_found。
    try:
        await ensure_cloud_profile_provisioned(
            db,
            agent_hasn_id=agent.agent_hasn_id,
            owner_hasn_id=agent.owner_hasn_id,
            profile_id=profile_id,
            trace_id=body.trace_id,
        )
    except HermesRuntimeError as exc:
        log.warning(f'cloud provision-on-demand failed for {profile_id}: {exc}; relay will report')
    generator = hasn_agent_runtime_dispatch_service.relay_run_stream(
        runtime_profile_id=profile_id,
        payload=body.payload,
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
    result = await hasn_agent_runtime_dispatch_service.cloud_runtime_health(
        runtime_profile_id=profile_id
    )
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
    return response_base.success(
        data=RuntimeRunCancelResponse(run_id=result['run_id'], cancelled=result['cancelled'])
    )
