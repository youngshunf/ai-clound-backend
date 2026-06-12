from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.hasn_growth.schema.business import CreateLeadJobParam, ExportLeadParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.post('/jobs', summary='创建采集任务', dependencies=[DependsJwtAuth])
async def create_job(request: Request, db: CurrentSessionTransaction, obj: CreateLeadJobParam) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.create_job(obj=obj.model_copy(update={'user_id': request.user.id}), db=db)
    )


@router.post('/jobs/{job_id}/run', summary='执行采集任务', dependencies=[DependsJwtAuth])
async def run_job(request: Request, db: CurrentSessionTransaction, job_id: int) -> ResponseModel:
    return response_base.success(data=await lead_automation_business_service.run_job(db, job_id, user_id=request.user.id))


@router.get('/jobs/{job_id}', summary='查询采集任务详情', dependencies=[DependsJwtAuth])
async def get_job(request: Request, db: CurrentSession, job_id: int) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.get_job(db, job_id=job_id, user_id=request.user.id)
    )


@router.get('/contacts', summary='查询有效线索', name='lead_automation_list_contacts', dependencies=[DependsJwtAuth])
async def list_contacts(request: Request, db: CurrentSession) -> ResponseModel:
    return response_base.success(data=await lead_automation_business_service.list_contacts(db, user_id=request.user.id))


@router.get('/rejected', summary='查询 rejected 记录', dependencies=[DependsJwtAuth])
async def list_rejected(request: Request, db: CurrentSession, job_id: int | None = None) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.list_rejected(db, user_id=request.user.id, job_id=job_id)
    )


@router.post('/exports', summary='导出 CSV', dependencies=[DependsJwtAuth])
async def export_contacts(request: Request, db: CurrentSessionTransaction, obj: ExportLeadParam) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.export_contacts(
            db,
            user_id=request.user.id,
            filter_payload=obj.filter_payload,
        )
    )
