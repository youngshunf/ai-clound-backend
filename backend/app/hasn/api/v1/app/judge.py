"""通用 LLM 裁判端点（Owner JWT app scope）。

事实源：docs/hasn-node设计文档/05-安全与权限/07-通用LLM裁判机制设计(内容披露与会话治理).md
       + 实施/07-通用LLM裁判机制实施清单.md（J-S1）

daemon 经 BackendGateway owner 通道调用（发起方恒为 daemon 入站/出站闸）：
  POST /api/v1/hasn/app/judge/{kind}  body {agent_hasn_id, peer_hasn_id, conversation_ref, payload}
- kind ∈ {termination, disclosure, node_review}（未知 kind → 422）；owner 身份取自 JWT（不信客户端传值）。
  - node_review = doc94 §5.3 工作流 W-S5 质量门（llm_judge 档）由 daemon 闸调用，与 termination/disclosure
    同属 daemon 侧闸——**不注册 Agent 工具 / 不进 MCP 面 / 不开 open scope**（清单 J-S1-3）。
- 统一信封返回（ResponseModel + response_base.success）：daemon transport decode_ok_envelope 依赖之。
"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn.service.judge_service import judge_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


class JudgeRequest(BaseModel):
    """裁判请求：kind 无关的公共字段 + kind 专属 payload。"""

    agent_hasn_id: str = Field(description='发起方分身 hasn_id')
    peer_hasn_id: str = Field(description='对端 hasn_id（人或分身）')
    conversation_ref: str = Field(default='', description='daemon 本地会话 id（仅溯源元数据）')
    payload: dict[str, Any] = Field(default_factory=dict, description='kind 专属字段（见 judge_service）')


@router.post(
    '/judge/{kind}',
    summary='通用 LLM 裁判（termination/disclosure/node_review；Owner JWT，daemon 闸调用）',
    dependencies=[DependsJwtAuth],
)
async def run_judge(
    db: CurrentSessionTransaction,
    kind: Annotated[str, Path(description='裁判类型 (termination/disclosure/node_review)')],
    request_body: JudgeRequest,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    owner_hasn_id = auth['hasn_id']  # 计费/凭据归属恒取 JWT 身份
    await judge_service.check_rate_limit(owner_hasn_id)
    verdict = await judge_service.judge(
        db,
        kind=kind,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=request_body.agent_hasn_id,
        peer_hasn_id=request_body.peer_hasn_id,
        conversation_ref=request_body.conversation_ref,
        payload=request_body.payload,
    )
    return response_base.success(data=verdict)
