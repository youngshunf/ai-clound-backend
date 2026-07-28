from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.hasn_growth.schema.business import DsrEmailParam, DsrPhoneParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/admin/contacts',
    summary='全量线索脱敏列表',
    name='lead_automation_admin_list_contacts',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:read_pii'))],
)
async def list_contacts(db: CurrentSession) -> ResponseModel:
    return response_base.success(data=await lead_automation_business_service.list_contacts(db, admin=True, masked=True))


@router.get(
    '/admin/audit-logs',
    summary='审计日志查询',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:audit'))],
)
async def list_audit_logs(
    db: CurrentSession,
    event_type: str | None = None,
    actor_user_id: int | None = None,
    target_table: str | None = None,
    limit: int = 100,
) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.list_audit_logs(
            db,
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_table=target_table,
            limit=limit,
        )
    )


@router.post(
    '/admin/archive-expired', summary='执行到期线索归档', dependencies=[DependsJwtAuth], name='admin_archive_expired'
)
async def archive_expired(db: CurrentSessionTransaction) -> ResponseModel:
    return response_base.success(data={'archived_count': await lead_automation_business_service.archive_expired(db)})


@router.post(
    '/admin/source-configs/blacklist',
    summary='维护来源黑名单',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:config'))],
)
async def update_blacklist(db: CurrentSessionTransaction, payload: dict) -> ResponseModel:
    return response_base.success(data=await lead_automation_business_service.update_blacklist(db, payload))


@router.delete(
    '/admin/contacts/by-email',
    summary='按 email 执行 DSR 删除',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:dsr'))],
)
async def delete_by_email(db: CurrentSessionTransaction, obj: DsrEmailParam) -> ResponseModel:
    audit = await lead_automation_business_service.dsr_delete_by_email(db, emails=obj.emails, request_id=obj.request_id)
    return response_base.success(data=audit)


@router.delete(
    '/admin/contacts/by-phone',
    summary='按 phone 执行 DSR 删除',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:dsr'))],
)
async def delete_by_phone(db: CurrentSessionTransaction, obj: DsrPhoneParam) -> ResponseModel:
    audit = await lead_automation_business_service.dsr_delete_by_phone(
        db,
        phones=obj.phones,
        country_hint=obj.country_hint,
        request_id=obj.request_id,
    )
    return response_base.success(data=audit)


@router.post(
    '/admin/contacts/{contact_id}/extend-retention',
    summary='延长线索保留期',
    dependencies=[DependsJwtAuth, Depends(RequestPermission('lead_automation:retention'))],
)
async def extend_retention(db: CurrentSessionTransaction, contact_id: int) -> ResponseModel:
    return response_base.success(
        data=await lead_automation_business_service.extend_retention(db, contact_id=contact_id)
    )
