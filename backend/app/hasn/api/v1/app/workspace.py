from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class SwitchWorkspaceRequest(BaseModel):
    kind: str
    enterprise_id: int | None = None


@router.get('/users/me/workspaces', dependencies=[DependsJwtAuth], summary='我的工作区列表')
async def list_my_workspaces(request: Request, db: CurrentSession) -> ResponseModel:
    data = await workbench_domain_service.list_user_workspaces(db, user_id=request.user.id)
    return response_base.success(data=data)


@router.post('/users/me/workspaces/active', dependencies=[DependsJwtAuth], summary='切换活跃工作区')
async def switch_active_workspace(
    request: Request, db: CurrentSessionTransaction, body: SwitchWorkspaceRequest
) -> ResponseModel:
    active = await workbench_domain_service.switch_active_workspace(
        db,
        user_id=request.user.id,
        kind=body.kind,
        enterprise_id=body.enterprise_id,
    )
    return response_base.success(data={'active': active})
