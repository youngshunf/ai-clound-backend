"""Marketplace templates app API."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class TemplateUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | str | None = None
    emoji: str | None = None
    icon_url: str | None = None
    skill_dependencies: list[str] | str | None = None
    sop_dependencies: list[str] | str | None = None


@router.get('', summary='List my templates', dependencies=[DependsJwtAuth])
async def list_my_templates(request: Request, db: CurrentSession) -> ResponseModel:
    return response_base.success(
        data={'items': await marketplace_template_service.list_user_templates(db=db, user_id=request.user.id)},
    )


@router.post('/upload', summary='Upload my template', dependencies=[DependsJwtAuth])
async def upload_my_template(
    request: Request,
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='Template ZIP package')],
    slug: Annotated[str | None, Form(description='Public slug')] = None,
    changelog: Annotated[str | None, Form(description='Changelog')] = None,
) -> ResponseModel:
    content = await file.read()
    template = await marketplace_template_service.upload_user_template(
        db=db,
        user_id=request.user.id,
        hasn_id=request.user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.get('/{resource_id:path}', summary='Get my template', dependencies=[DependsJwtAuth])
async def get_my_template(request: Request, db: CurrentSession, resource_id: str) -> ResponseModel:
    template = await marketplace_template_service.get_by_resource_id_for_user(
        db=db,
        resource_id=resource_id,
        user_id=request.user.id,
    )
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post(
    '/{resource_id:path}/submit-review',
    summary='Submit my template for review',
    dependencies=[DependsJwtAuth],
)
async def submit_my_template_review(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    template = await marketplace_template_service.submit_review(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/{resource_id:path}/publish', summary='Publish my template', dependencies=[DependsJwtAuth])
async def publish_my_template(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    template = await marketplace_template_service.publish(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/{resource_id:path}/unpublish', summary='Unpublish my template', dependencies=[DependsJwtAuth])
async def unpublish_my_template(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    template = await marketplace_template_service.unpublish(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.patch('/{resource_id:path}', summary='Update my template metadata', dependencies=[DependsJwtAuth])
async def update_my_template(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: TemplateUpdateBody,
) -> ResponseModel:
    template = await marketplace_template_service.update_user_template(
        db=db,
        resource_id=resource_id,
        user_id=request.user.id,
        payload=body.model_dump(exclude_none=True),
    )
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.delete('/{resource_id:path}', summary='Delete my template', dependencies=[DependsJwtAuth])
async def delete_my_template(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    await marketplace_template_service.delete_user_template(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success()
