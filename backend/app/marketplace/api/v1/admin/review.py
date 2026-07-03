"""Marketplace resource review API."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSessionTransaction

router = APIRouter(dependencies=[DependsRBAC])


class ReviewBody(BaseModel):
    review_note: str | None = None
    suspend_reason: str | None = None


@router.post('/skills/{resource_id:path}/approve', summary='Approve skill')
async def approve_skill(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    skill = await marketplace_skill_service.approve(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        review_note=body.review_note if body else None,
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/skills/{resource_id:path}/reject', summary='Reject skill')
async def reject_skill(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    skill = await marketplace_skill_service.reject(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        review_note=body.review_note if body else None,
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/skills/{resource_id:path}/suspend', summary='Suspend skill')
async def suspend_skill(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    skill = await marketplace_skill_service.suspend(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        suspend_reason=body.suspend_reason if body else None,
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/templates/{resource_id:path}/approve', summary='Approve template')
async def approve_template(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    template = await marketplace_template_service.approve(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        review_note=body.review_note if body else None,
    )
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/templates/{resource_id:path}/reject', summary='Reject template')
async def reject_template(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    template = await marketplace_template_service.reject(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        review_note=body.review_note if body else None,
    )
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/templates/{resource_id:path}/suspend', summary='Suspend template')
async def suspend_template(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: ReviewBody | None = None,
) -> ResponseModel:
    template = await marketplace_template_service.suspend(
        db=db,
        resource_id=resource_id,
        reviewer_id=getattr(request.user, 'id', None),
        suspend_reason=body.suspend_reason if body else None,
    )
    return response_base.success(data=marketplace_template_service.format_template(template))
