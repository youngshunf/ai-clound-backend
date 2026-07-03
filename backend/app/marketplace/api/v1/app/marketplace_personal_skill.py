"""个人技能库 app API（Owner JWT，SKILLSYNC-C2）。

owner 维度的"个人技能管理"：列表/详情/删除、从技能 zip 包 upsert（daemon 同步与目录上传都走这里）、
一键发布到市场、装配到分身（写 hasn_agents.skills 触发跨设备物化）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from backend.app.hasn.service.hasn_agents_service import agent_profile_service
from backend.app.marketplace.service.marketplace_personal_skill_service import personal_skill_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class AttachBody(BaseModel):
    agent_hasn_id: str


class PublishBody(BaseModel):
    changelog: str | None = None


@router.get('', summary='List my personal skills', dependencies=[DependsJwtAuth])
async def list_my_personal_skills(request: Request, db: CurrentSession) -> ResponseModel:
    return response_base.success(
        data={'items': await personal_skill_service.list_mine(db, user_id=request.user.id)},
    )


@router.post('/upload', summary='Upsert a personal skill from package', dependencies=[DependsJwtAuth])
async def upload_personal_skill(
    request: Request,
    db: CurrentSessionTransaction,
    file: Annotated[UploadFile, File(description='Skill ZIP package')],
    origin: Annotated[str, Form(description='runtime-auto | user-upload | agent-published')] = 'user-upload',
    slug: Annotated[str | None, Form(description='Slug override')] = None,
    content_hash: Annotated[str | None, Form(description='调用方算的稳定内容哈希（去重用）')] = None,
    source_agent_hasn_id: Annotated[str | None, Form(description='来源分身（runtime-auto/agent-published）')] = None,
    agent_hasn_id: Annotated[str | None, Form(description='upsert 后装配到该分身（写 skills）')] = None,
) -> ResponseModel:
    content = await file.read()
    skill = await personal_skill_service.upsert_from_package(
        db,
        user_id=request.user.id,
        hasn_id=request.user.hasn_id,
        content=content,
        origin=origin,
        source_agent_hasn_id=source_agent_hasn_id,
        content_hash=content_hash,
        slug=slug,
    )
    attached = None
    if agent_hasn_id:
        await agent_profile_service.attach_personal_skill_cloud_first(
            db,
            owner_id=request.user.hasn_id,
            hasn_id=agent_hasn_id,
            personal_skill_id=skill.personal_skill_id,
            user_id=request.user.id,
        )
        attached = agent_hasn_id
    return response_base.success(
        data={**personal_skill_service.format(skill), 'attached_agent_hasn_id': attached},
    )


@router.post('/{personal_skill_id:path}/attach', summary='Attach personal skill to an agent', dependencies=[DependsJwtAuth])
async def attach_personal_skill(
    request: Request,
    db: CurrentSessionTransaction,
    personal_skill_id: str,
    body: AttachBody,
) -> ResponseModel:
    await agent_profile_service.attach_personal_skill_cloud_first(
        db,
        owner_id=request.user.hasn_id,
        hasn_id=body.agent_hasn_id,
        personal_skill_id=personal_skill_id,
        user_id=request.user.id,
    )
    return response_base.success()


@router.post('/{personal_skill_id:path}/publish', summary='Publish personal skill to marketplace', dependencies=[DependsJwtAuth])
async def publish_personal_skill(
    request: Request,
    db: CurrentSessionTransaction,
    personal_skill_id: str,
    body: PublishBody,
) -> ResponseModel:
    result = await personal_skill_service.promote_to_market(
        db,
        user_id=request.user.id,
        hasn_id=request.user.hasn_id,
        personal_skill_id=personal_skill_id,
        changelog=body.changelog,
    )
    return response_base.success(data=result)


@router.delete('/{personal_skill_id:path}', summary='Delete my personal skill', dependencies=[DependsJwtAuth])
async def delete_personal_skill(
    request: Request, db: CurrentSessionTransaction, personal_skill_id: str
) -> ResponseModel:
    await personal_skill_service.delete_mine(db, user_id=request.user.id, personal_skill_id=personal_skill_id)
    return response_base.success()


@router.get('/{personal_skill_id:path}', summary='Get my personal skill', dependencies=[DependsJwtAuth])
async def get_personal_skill(request: Request, db: CurrentSession, personal_skill_id: str) -> ResponseModel:
    skill = await personal_skill_service.get_mine(db, user_id=request.user.id, personal_skill_id=personal_skill_id)
    return response_base.success(data=personal_skill_service.format(skill))
