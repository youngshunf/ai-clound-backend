"""Marketplace skills app API."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class SkillUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | str | None = None
    emoji: str | None = None
    icon_url: str | None = None


@router.get('', summary='List my skills', dependencies=[DependsJwtAuth])
async def list_my_skills(request: Request, db: CurrentSession) -> ResponseModel:
    return response_base.success(
        data={'items': await marketplace_skill_service.list_user_skills(db=db, user_id=request.user.id)},
    )


@router.post('/upload', summary='Upload my skill', dependencies=[DependsJwtAuth])
async def upload_my_skill(
    request: Request,
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='Skill ZIP package')],
    slug: Annotated[str | None, Form(description='Public slug')] = None,
    changelog: Annotated[str | None, Form(description='Changelog')] = None,
) -> ResponseModel:
    content = await file.read()
    skill = await marketplace_skill_service.upload_user_skill(
        db=db,
        user_id=request.user.id,
        hasn_id=request.user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.get('/{resource_id:path}', summary='Get my skill', dependencies=[DependsJwtAuth])
async def get_my_skill(request: Request, db: CurrentSession, resource_id: str) -> ResponseModel:
    skill = await marketplace_skill_service.get_by_resource_id_for_user(
        db=db,
        resource_id=resource_id,
        user_id=request.user.id,
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/{resource_id:path}/submit-review', summary='Submit my skill for review', dependencies=[DependsJwtAuth])
async def submit_my_skill_review(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    skill = await marketplace_skill_service.submit_review(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/{resource_id:path}/publish', summary='Publish my skill', dependencies=[DependsJwtAuth])
async def publish_my_skill(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    skill = await marketplace_skill_service.publish(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.post('/{resource_id:path}/unpublish', summary='Unpublish my skill', dependencies=[DependsJwtAuth])
async def unpublish_my_skill(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    skill = await marketplace_skill_service.unpublish(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.patch('/{resource_id:path}', summary='Update my skill metadata', dependencies=[DependsJwtAuth])
async def update_my_skill(
    request: Request,
    db: CurrentSessionTransaction,
    resource_id: str,
    body: SkillUpdateBody,
) -> ResponseModel:
    skill = await marketplace_skill_service.update_user_skill(
        db=db,
        resource_id=resource_id,
        user_id=request.user.id,
        payload=body.model_dump(exclude_none=True),
    )
    return response_base.success(data=marketplace_skill_service.format_skill(skill))


@router.delete('/{resource_id:path}', summary='Delete my skill', dependencies=[DependsJwtAuth])
async def delete_my_skill(request: Request, db: CurrentSessionTransaction, resource_id: str) -> ResponseModel:
    await marketplace_skill_service.delete_user_skill(db=db, resource_id=resource_id, user_id=request.user.id)
    return response_base.success()
