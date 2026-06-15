from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, Field

from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction  # noqa: TC001
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.utils.file_ops import build_object_url, pick_public_storage, write_bytes
from backend.utils.file_ops import upload_file_verify

router = APIRouter()


class CreateEnterpriseRequest(BaseModel):
    name: str = Field(description='企业名称')
    slug: str | None = None
    description: str | None = None
    logo: str | None = None
    industry: str | None = None
    company_size: str | None = None
    join_policy: str = 'invite_only'


class ApplyEnterpriseRequest(BaseModel):
    apply_message: str | None = None
    invite_code: str | None = None


class RejectApplicationRequest(BaseModel):
    note: str | None = None


class CreateInviteCodeRequest(BaseModel):
    max_uses: int | None = None
    expires_at: datetime | None = None
    auto_approve: bool = False


class CreateRoleRequest(BaseModel):
    name: str = Field(description='角色 / 部门名称')
    kind: str = Field(default='role', description='类型：role 角色 / department 部门')


class UpdateRoleRequest(BaseModel):
    name: str | None = None
    kind: str | None = None


@router.post('/enterprises', dependencies=[DependsJwtAuth], summary='创建企业')
async def create_enterprise(
    request: Request, db: CurrentSessionTransaction, body: CreateEnterpriseRequest
) -> ResponseModel:
    data = await workbench_domain_service.create_enterprise(
        db,
        user_id=request.user.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        logo=body.logo,
        industry=body.industry,
        company_size=body.company_size,
        join_policy=body.join_policy,
    )
    return response_base.success(data=data)


@router.post('/enterprises/logo/upload', dependencies=[DependsJwtAuth], summary='上传企业 Logo')
async def upload_enterprise_logo(
    request: Request,
    db: CurrentSession,
    file: Annotated[UploadFile, File(description='企业 Logo 文件')],
) -> ResponseModel:
    upload_file_verify(file)
    storages = await s3_storage_dao.get_all(db)
    s3_storage = pick_public_storage(storages)
    if not s3_storage:
        raise errors.RequestError(msg='S3 存储配置不存在，无法上传企业 Logo')

    content = await file.read()
    file_ext = file.filename.split('.')[-1].lower() if file.filename and '.' in file.filename else 'png'
    file_hash = hashlib.md5(content).hexdigest()[:8]
    filename = f'enterprise-logos/{request.user.id}_{file_hash}.{file_ext}'
    await write_bytes(s3_storage, filename, content, file.content_type)
    return response_base.success(data={'url': build_object_url(s3_storage, filename)})


@router.get('/enterprises/search', dependencies=[DependsJwtAuth], summary='搜索企业')
async def search_enterprises(db: CurrentSession, q: str = '') -> ResponseModel:
    return response_base.success(data=await workbench_domain_service.search_enterprises(db, q=q))


@router.get('/enterprises/{enterprise_id}', dependencies=[DependsJwtAuth], summary='企业详情')
async def get_enterprise(db: CurrentSession, enterprise_id: int) -> ResponseModel:
    return response_base.success(data=await workbench_domain_service.get_enterprise(db, enterprise_id))


@router.patch('/enterprises/{enterprise_id}', dependencies=[DependsJwtAuth], summary='更新企业')
async def update_enterprise(db: CurrentSessionTransaction, enterprise_id: int, body: dict[str, Any]) -> ResponseModel:
    data = await workbench_domain_service.update_enterprise(db, enterprise_id=enterprise_id, updates=body)
    return response_base.success(data=data)


@router.delete('/enterprises/{enterprise_id}', dependencies=[DependsJwtAuth], summary='解散企业')
async def delete_enterprise(db: CurrentSessionTransaction, enterprise_id: int) -> ResponseModel:
    await workbench_domain_service.delete_enterprise(db, enterprise_id=enterprise_id)
    return response_base.success()


@router.get('/enterprises/{enterprise_id}/members', dependencies=[DependsJwtAuth], summary='企业成员')
async def list_members(db: CurrentSession, enterprise_id: int) -> ResponseModel:
    return response_base.success(data=await workbench_domain_service.list_members(db, enterprise_id=enterprise_id))


@router.post('/enterprises/{enterprise_id}/applications', dependencies=[DependsJwtAuth], summary='申请加入企业')
async def apply_enterprise(
    request: Request,
    db: CurrentSessionTransaction,
    enterprise_id: int,
    body: ApplyEnterpriseRequest,
) -> ResponseModel:
    data = await workbench_domain_service.apply_enterprise(
        db,
        enterprise_id=enterprise_id,
        user_id=request.user.id,
        apply_message=body.apply_message,
        invite_code=body.invite_code,
    )
    return response_base.success(data=data)


@router.get('/enterprises/{enterprise_id}/applications', dependencies=[DependsJwtAuth], summary='企业申请列表')
async def list_applications(db: CurrentSession, enterprise_id: int, status: str = 'pending') -> ResponseModel:
    data = await workbench_domain_service.list_applications(db, enterprise_id=enterprise_id, status=status)
    return response_base.success(data=data)


@router.post(
    '/enterprises/{enterprise_id}/applications/{app_id}/approve', dependencies=[DependsJwtAuth], summary='通过申请'
)
async def approve_application(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, app_id: int
) -> ResponseModel:
    data = await workbench_domain_service.approve_application(
        db,
        enterprise_id=enterprise_id,
        app_id=app_id,
        decided_by=request.user.id,
    )
    return response_base.success(data=data)


@router.post(
    '/enterprises/{enterprise_id}/applications/{app_id}/reject', dependencies=[DependsJwtAuth], summary='拒绝申请'
)
async def reject_application(
    request: Request,
    db: CurrentSessionTransaction,
    enterprise_id: int,
    app_id: int,
    body: RejectApplicationRequest,
) -> ResponseModel:
    data = await workbench_domain_service.reject_application(
        db,
        enterprise_id=enterprise_id,
        app_id=app_id,
        decided_by=request.user.id,
        note=body.note,
    )
    return response_base.success(data=data)


@router.delete(
    '/enterprises/{enterprise_id}/members/{user_id}', dependencies=[DependsJwtAuth], summary='退出或移除成员'
)
async def remove_member(db: CurrentSessionTransaction, enterprise_id: int, user_id: int) -> ResponseModel:
    await workbench_domain_service.remove_member(db, enterprise_id=enterprise_id, user_id=user_id)
    return response_base.success()


@router.get('/enterprises/{enterprise_id}/invite-codes', dependencies=[DependsJwtAuth], summary='邀请码列表')
async def list_invite_codes(db: CurrentSession, enterprise_id: int) -> ResponseModel:
    return response_base.success(data=await workbench_domain_service.list_invite_codes(db, enterprise_id=enterprise_id))


@router.post('/enterprises/{enterprise_id}/invite-codes', dependencies=[DependsJwtAuth], summary='生成邀请码')
async def create_invite_code(
    request: Request,
    db: CurrentSessionTransaction,
    enterprise_id: int,
    body: CreateInviteCodeRequest,
) -> ResponseModel:
    data = await workbench_domain_service.create_invite_code(
        db,
        enterprise_id=enterprise_id,
        created_by=request.user.id,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
        auto_approve=body.auto_approve,
    )
    return response_base.success(data=data)


@router.delete('/enterprises/{enterprise_id}/invite-codes/{code}', dependencies=[DependsJwtAuth], summary='撤销邀请码')
async def revoke_invite_code(db: CurrentSessionTransaction, enterprise_id: int, code: str) -> ResponseModel:
    data = await workbench_domain_service.revoke_invite_code(db, enterprise_id=enterprise_id, code=code)
    return response_base.success(data=data)


# ── 企业角色 / 部门管理（应用平台 v3 P3 §4.2(4)/§6.5）──────────────────────────
# owner / admin 鉴权与跨企业隔离在 workbench_domain_service 内强制（operator_user_id=请求人）。


@router.get('/enterprises/{enterprise_id}/roles', dependencies=[DependsJwtAuth], summary='企业角色 / 部门列表')
async def list_roles(request: Request, db: CurrentSession, enterprise_id: int) -> ResponseModel:
    data = await workbench_domain_service.list_roles(
        db, enterprise_id=enterprise_id, operator_user_id=request.user.id
    )
    return response_base.success(data=data)


@router.post('/enterprises/{enterprise_id}/roles', dependencies=[DependsJwtAuth], summary='创建角色 / 部门')
async def create_role(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, body: CreateRoleRequest
) -> ResponseModel:
    data = await workbench_domain_service.create_role(
        db,
        enterprise_id=enterprise_id,
        operator_user_id=request.user.id,
        name=body.name,
        kind=body.kind,
    )
    return response_base.success(data=data)


@router.patch('/enterprises/{enterprise_id}/roles/{role_id}', dependencies=[DependsJwtAuth], summary='改名 / 改类型')
async def update_role(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, role_id: int, body: UpdateRoleRequest
) -> ResponseModel:
    data = await workbench_domain_service.update_role(
        db,
        enterprise_id=enterprise_id,
        operator_user_id=request.user.id,
        role_id=role_id,
        name=body.name,
        kind=body.kind,
    )
    return response_base.success(data=data)


@router.delete('/enterprises/{enterprise_id}/roles/{role_id}', dependencies=[DependsJwtAuth], summary='删除角色 / 部门')
async def delete_role(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, role_id: int
) -> ResponseModel:
    await workbench_domain_service.delete_role(
        db, enterprise_id=enterprise_id, operator_user_id=request.user.id, role_id=role_id
    )
    return response_base.success()


@router.get(
    '/enterprises/{enterprise_id}/roles/{role_id}/members', dependencies=[DependsJwtAuth], summary='角色成员列表'
)
async def list_role_members(
    request: Request, db: CurrentSession, enterprise_id: int, role_id: int
) -> ResponseModel:
    data = await workbench_domain_service.list_role_members(
        db, enterprise_id=enterprise_id, operator_user_id=request.user.id, role_id=role_id
    )
    return response_base.success(data=data)


@router.post(
    '/enterprises/{enterprise_id}/roles/{role_id}/members/{user_id}',
    dependencies=[DependsJwtAuth],
    summary='授予成员角色 / 部门',
)
async def grant_member_role(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, role_id: int, user_id: int
) -> ResponseModel:
    data = await workbench_domain_service.grant_member_role(
        db,
        enterprise_id=enterprise_id,
        operator_user_id=request.user.id,
        role_id=role_id,
        user_id=user_id,
    )
    return response_base.success(data=data)


@router.delete(
    '/enterprises/{enterprise_id}/roles/{role_id}/members/{user_id}',
    dependencies=[DependsJwtAuth],
    summary='撤销成员角色 / 部门',
)
async def revoke_member_role(
    request: Request, db: CurrentSessionTransaction, enterprise_id: int, role_id: int, user_id: int
) -> ResponseModel:
    await workbench_domain_service.revoke_member_role(
        db,
        enterprise_id=enterprise_id,
        operator_user_id=request.user.id,
        role_id=role_id,
        user_id=user_id,
    )
    return response_base.success()
