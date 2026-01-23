from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.topic.crud.init_data import init_industry_data
from backend.app.topic.schema.topic import (
    CreateIndustryParam,
    CreateTopicParam,
    IndustrySchema,
    TopicGenerateResponse,
    TopicSchema,
    UpdateIndustryParam,
    UpdateTopicParam,
)
from backend.app.topic.service.industry_service import IndustryService
from backend.app.topic.service.topic_service import TopicService
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import get_db

router = APIRouter()


@router.post("/init", summary="初始化行业数据", dependencies=[DependsJwtAuth])
async def init_data(db: Annotated[AsyncSession, Depends(get_db)]):
    await init_industry_data(db)
    return response_base.success()


@router.get("/industries", response_model=ResponseSchemaModel[list[IndustrySchema]], summary="获取行业列表", dependencies=[DependsJwtAuth])
async def get_industries(db: Annotated[AsyncSession, Depends(get_db)]):
    data = await IndustryService.get_industry_tree(db)
    return response_base.success(data=data)


@router.get("/recommendations", response_model=ResponseSchemaModel[list[TopicSchema]], summary="获取推荐选题", dependencies=[DependsJwtAuth])
async def get_recommendations(
    industry_id: int | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    data = await TopicService.get_recommendations(db, industry_id, limit)
    return response_base.success(data=data)


@router.post("/generate", response_model=ResponseSchemaModel[TopicGenerateResponse], summary="触发选题生成(测试用)", dependencies=[DependsJwtAuth])
async def generate_topics(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await TopicService.generate_daily_topics(db)
    return response_base.success(data=result)


# Industry CRUD
@router.post("/industry", summary="创建行业", dependencies=[DependsJwtAuth])
async def create_industry(
    obj: CreateIndustryParam,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await IndustryService.create(db, obj)
    return response_base.success()


@router.put("/industry/{pk}", summary="更新行业", dependencies=[DependsJwtAuth])
async def update_industry(
    pk: Annotated[int, Path(...)],
    obj: UpdateIndustryParam,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await IndustryService.update(db, pk, obj)
    return response_base.success()


@router.delete("/industry/{pk}", summary="删除行业", dependencies=[DependsJwtAuth])
async def delete_industry(
    pk: Annotated[int, Path(...)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await IndustryService.delete(db, pk)
    return response_base.success()


# Topic CRUD
@router.post("/topic", summary="创建选题", dependencies=[DependsJwtAuth])
async def create_topic(
    obj: CreateTopicParam,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await TopicService.create(db, obj)
    return response_base.success()


@router.put("/topic/{pk}", summary="更新选题", dependencies=[DependsJwtAuth])
async def update_topic(
    pk: Annotated[int, Path(...)],
    obj: UpdateTopicParam,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await TopicService.update(db, pk, obj)
    return response_base.success()


@router.delete("/topic/{pk}", summary="删除选题", dependencies=[DependsJwtAuth])
async def delete_topic(
    pk: Annotated[int, Path(...)],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    await TopicService.delete(db, pk)
    return response_base.success()
