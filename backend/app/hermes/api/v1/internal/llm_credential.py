"""Hermes runtime internal LLM credential endpoints（§09 §5）。

仅 backend → runtime 单向 service-token 调用，不暴露给浏览器。
所有 endpoint 必须携带 X-Internal-Token header（require_runtime_internal_token）。

路由前缀：/api/v1/hermes/internal/llm
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.newapi.service import (
    llm_newapi_user_mapping_service,
)
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.internal_auth import require_runtime_internal_token
from backend.database.db import CurrentSession
from backend.utils.timezone import timezone as _tz


router = APIRouter()


class IssueCredentialPayload(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    user_id: int = Field(..., ge=1)
    model_allowlist: list[str] | None = None
    rate_limit_rps: int | None = Field(None, ge=0)
    per_token_quota: int | None = Field(None, ge=0)


@router.post(
    '/issue-credential',
    summary='Hermes runtime: 为 Agent 签发 newapi 短期凭证（§09 §5）',
    dependencies=[Depends(require_runtime_internal_token)],
)
async def issue_credential(
    db: CurrentSession,
    payload: IssueCredentialPayload,
) -> ResponseModel:
    """签发或复用 Agent 级 newapi token。

    - 同 agent_id 已有未撤销记录 → 返回已存在记录（raw_token_key=None, reused=True）
    - 否则签发新 token（raw_token_key 仅此次返回，DB 只存 prefix + sha256）
    """
    issued = await llm_newapi_user_mapping_service.ensure_agent_token(
        db,
        agent_id=payload.agent_id,
        user_id=payload.user_id,
        model_allowlist=payload.model_allowlist,
        rate_limit_rps=payload.rate_limit_rps,
        per_token_quota=payload.per_token_quota,
    )
    return response_base.success(
        data={
            'agent_id': issued['agent_id'],
            'newapi_user_id': issued['newapi_user_id'],
            'newapi_token_id': issued['newapi_token_id'],
            'token_key_prefix': issued['token_key_prefix'],
            'raw_token_key': issued['raw_token_key'],
            'reused': issued['reused'],
            'issued_at': _tz.now().isoformat(),
        }
    )


class RevokeCredentialPayload(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)


@router.post(
    '/revoke-credential',
    summary='Hermes runtime: 撤销 Agent 当前 newapi 凭证（§09 §5）',
    dependencies=[Depends(require_runtime_internal_token)],
)
async def revoke_credential(
    db: CurrentSession,
    payload: RevokeCredentialPayload,
) -> ResponseModel:
    """撤销 Agent 当前未撤销的 token。幂等：已撤销/不存在 → revoked=False。"""
    revoked = await llm_newapi_user_mapping_service.revoke_agent_token(
        db, payload.agent_id,
    )
    return response_base.success(
        data={
            'agent_id': payload.agent_id,
            'revoked': revoked,
            'revoked_at': _tz.now().isoformat() if revoked else None,
        }
    )


class RotateCredentialPayload(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    user_id: int = Field(..., ge=1)


@router.post(
    '/rotate-credential',
    summary='Hermes runtime: 旋转 Agent newapi 凭证（先撤旧再签新，§09 §5）',
    dependencies=[Depends(require_runtime_internal_token)],
)
async def rotate_credential(
    db: CurrentSession,
    payload: RotateCredentialPayload,
) -> ResponseModel:
    """旋转 Agent token：先撤销旧 token，再签发新 token。

    raw_token_key 仅本接口返回；DB 只存 prefix + sha256。
    """
    issued = await llm_newapi_user_mapping_service.rotate_agent_token(
        db,
        agent_id=payload.agent_id,
        user_id=payload.user_id,
    )
    return response_base.success(
        data={
            'agent_id': issued['agent_id'],
            'newapi_user_id': issued['newapi_user_id'],
            'newapi_token_id': issued['newapi_token_id'],
            'token_key_prefix': issued['token_key_prefix'],
            'raw_token_key': issued['raw_token_key'],
            'issued_at': _tz.now().isoformat(),
        }
    )
