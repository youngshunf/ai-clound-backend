"""分身产物 - Agent 端 API（Agent JWT）。

路由前缀: /api/v1/artifacts/agent
认证方式: Agent JWT（Authorization: Bearer <agent_jwt>，见 common.security.agent_jwt_auth）

身份模型（重要）:
- agent / owner 身份**恒取自 Agent JWT**（agent.agent_hasn_id / agent.owner_hasn_id），
  **绝不**从请求体读取身份字段（防越权登记到别人名下）。

这是 hasn-mcp `audited` 自动捕获钩子上报产物的唯一入口（设计 §4.3/§6.1）。
"""

from typing import Annotated

from fastapi import APIRouter

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam, RecordArtifactResult
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


@router.post('/record', summary='Agent 登记一条产物（自动捕获上报，去重幂等）')
async def record_artifact(
    db: CurrentSessionTransaction,
    obj: RecordArtifactParam,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
) -> ResponseSchemaModel[RecordArtifactResult]:
    """登记产物。身份取自 Agent JWT；去重键 (agent, dispatch_id, asset_id) 命中则返回既有 id。"""
    artifact_id = await hasn_artifacts_service.record(
        db,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        params=obj,
    )
    return response_base.success(data=RecordArtifactResult(artifact_id=artifact_id))
