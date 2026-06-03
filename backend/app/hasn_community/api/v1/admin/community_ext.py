"""社区扩展管理端 API（话题运营，Admin JWT）。

路由前缀: /api/v1/community/admin。话题合并/置顶/封禁/改名（见 15 §4.4）。
圈子/文集的管理端只读审核沿用各自 open/app 读路径，本期写治理仅话题运营。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


class MergeTopicRequest(BaseModel):
    target: str = Field(..., description='合并目标话题 slug 或 topic_id')


class FeatureTopicRequest(BaseModel):
    featured: bool = True


class UpdateTopicRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cover_url: str | None = None


@router.post('/topics/{ident}/merge', summary='合并话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def admin_merge_topic(db: CurrentSessionTransaction, ident: str, body: MergeTopicRequest) -> ResponseModel:
    return response_base.success(data=await topic_service.merge_topic(db, ident=ident, target_ident=body.target))


@router.post('/topics/{ident}/feature', summary='置顶/取消推荐话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def admin_feature_topic(db: CurrentSessionTransaction, ident: str, body: FeatureTopicRequest) -> ResponseModel:
    return response_base.success(data=await topic_service.feature_topic(db, ident=ident, featured=body.featured))


@router.post('/topics/{ident}/block', summary='封禁话题', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def admin_block_topic(db: CurrentSessionTransaction, ident: str) -> ResponseModel:
    return response_base.success(data=await topic_service.block_topic(db, ident=ident))


@router.put('/topics/{ident}', summary='改话题名/描述/封面', dependencies=[DependsJwtAuth], response_model=ResponseModel)
async def admin_update_topic(db: CurrentSessionTransaction, ident: str, body: UpdateTopicRequest) -> ResponseModel:
    return response_base.success(data=await topic_service.update_topic(db, ident=ident, name=body.name, description=body.description, cover_url=body.cover_url))
