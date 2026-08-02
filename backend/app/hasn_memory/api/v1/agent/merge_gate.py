"""云端合并闸 Agent 端 API（doc19 §5.5 / §5.6）。

认证方式：`DependsAgentJwtAuth`（Agent JWT）。**owner / agent 身份恒取自凭证**，绝不从请求体
或路径读取——铁律「agent scope 由 Agent JWT 或 Agent MCP Key 自识别，禁止 X-User-Id」。

URL：`/api/v1/hasn/memory/agent/merge/{apply,request}`（挂在 hasn v1 前缀下，与既有
`/api/v1/hasn/memory/sync/pull` 同一「memory」资源域）。

- `apply`：主脑分身提交**整轮**合并结果。整轮原子，六步校验任一不符 409 + `rejected_reason`；
- `request`：非主脑分身请求合并，主脑离线时落云端每 owner 待办（去重只留最新，不堆积）。
"""

from typing import Annotated

from fastapi import APIRouter

from backend.app.hasn.service.sync_invalidate_service import KIND_AGENTS, KIND_MEMORY, bump_owner
from backend.app.hasn_memory.schema.merge_gate import (
    MergeApplyRequest,
    MergeApplyResponse,
    MergeRequestBody,
    MergeRequestResponse,
)
from backend.app.hasn_memory.service.merge_gate_service import MergeGateRejectedError, merge_gate_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSessionTransaction, async_db_session

router = APIRouter()


def _changes_agent_profile(body: MergeApplyRequest) -> bool:
    """本轮是否改了 USER.md / MEMORY.md，决定是否发布 agents 失效。"""
    owner_changed = bool(
        body.owner_memory
        and (body.owner_memory.clear or (body.owner_memory.content or '').strip())
    )
    return owner_changed or bool(body.agent_self_portraits)


async def _publish_committed_invalidations(owner_id: str, *, agents_changed: bool) -> None:
    """在合并事务提交后重算 revision 并推送；失败不反悔已提交的权威写。"""
    kinds = (KIND_MEMORY, KIND_AGENTS) if agents_changed else (KIND_MEMORY,)
    for kind in kinds:
        try:
            async with async_db_session() as snapshot_db:
                await bump_owner(kind, snapshot_db, owner_id)
        except Exception as exc:
            log.warning(f'合并闸提交后 WSPUSH 失败 kind={kind} owner={owner_id}: {exc}')


@router.post(
    '/merge/apply',
    summary='主脑分身提交整轮合并结果（云端合并闸）',
)
async def apply_merge(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    body: MergeApplyRequest,
) -> ResponseSchemaModel[MergeApplyResponse]:
    """整轮原子应用（doc19 §5.6）。

    拒绝时返回 **409** + `data.rejected_reason`（`not_master_brain` / `version_conflict` /
    `owner_edit_conflict` / `fact_snapshot_conflict` / `run_id_owner_mismatch`），并已在独立事务里登记
    `merge_run(status='rejected')`——主脑据此
    下轮重跑，主人在记忆页看得到「为什么没整理成」，不静默停摆。
    """
    try:
        result = await merge_gate_service.apply(
            db,
            owner_id=agent.owner_hasn_id,
            agent_id=agent.agent_hasn_id,
            body=body,
        )
    except MergeGateRejectedError as rejected:
        raise errors.ConflictError(
            msg=rejected.message,
            data={
                'applied': False,
                'run_id': body.run_id,
                'rejected_reason': rejected.reason,
                'detail': rejected.detail,
            },
        ) from rejected
    # CurrentSessionTransaction 原本在依赖退出时才提交；invalidate 若在 service 内发布，在线
    # daemon 会回源读到旧快照。这里先明确提交，再用新会话按已提交数据计算 revision 并推送。
    await db.commit()
    if not result.replayed:
        await _publish_committed_invalidations(
            agent.owner_hasn_id,
            agents_changed=_changes_agent_profile(body),
        )
    return response_base.success(data=result)


@router.post(
    '/merge/request',
    summary='非主脑分身请求合并（主脑离线时落云端待办）',
)
async def request_merge(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSessionTransaction,
    body: MergeRequestBody,
) -> ResponseSchemaModel[MergeRequestResponse]:
    """登记合并待办（doc19 §5.5）：每主人至多一条，重复请求覆盖为最新，**永不排队堆积**。"""
    result = await merge_gate_service.request_merge(
        db,
        owner_id=agent.owner_hasn_id,
        agent_id=agent.agent_hasn_id,
        body=body,
    )
    return response_base.success(data=result)
