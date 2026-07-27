"""new-api 用量查询与额度 — 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 自动关联 new-api 映射

§09 §5: usage/summary 支持 ?agent_id=... 切换到 per-Agent 聚合，
跨用户访问的 agent_id 一律 403（hermes_agent.user_id 校验）。
"""

import time

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from backend.app.hermes.model import HermesAgent
from backend.app.newapi.schema.llm_newapi_user_mapping import (
    NewApiMappingInfo,
    NewApiQuotaInfo,
    NewApiUsageDetail,
)
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/quota',
    summary='查询用户 LLM 剩余额度',
    dependencies=[DependsJwtAuth],
)
async def get_newapi_quota(request: Request, db: CurrentSession) -> ResponseSchemaModel[NewApiQuotaInfo]:
    data = await llm_newapi_user_mapping_service.get_quota_info(db, request.user.id)
    return response_base.success(data=data)


@router.get(
    '/usage/summary',
    summary='查询用量统计概览（按模型分组；可选 ?agent_id 切到 per-Agent 聚合）',
    dependencies=[DependsJwtAuth],
)
async def get_newapi_usage_summary(
    request: Request,
    db: CurrentSession,
    start_time: Annotated[int | None, Query(description='开始时间 (unix timestamp)')] = None,
    end_time: Annotated[int | None, Query(description='结束时间 (unix timestamp)')] = None,
    agent_id: Annotated[
        str | None,
        Query(description='可选：传入则按 per-Agent 聚合（hermes_agent.user_id 必须 == 当前用户）'),
    ] = None,
) -> ResponseSchemaModel:
    """§09 §5：

    - 不传 agent_id → 兼容历史，按 user_id 聚合（NewApiUsageSummary）
    - 传 agent_id → 校验归属（跨用户 403），按 per-Agent 聚合（dict）
    """
    now = int(time.time())
    if not start_time:
        start_time = now - 30 * 86400
    if not end_time:
        end_time = now

    if agent_id is None:
        usage_summary = await llm_newapi_user_mapping_service.get_usage_summary(
            db, request.user.id, start_time, end_time,
        )
        return response_base.success(data=usage_summary)

    # per-Agent 路径：先校验 hermes_agent.user_id == request.user.id
    stmt = select(HermesAgent.user_id).where(HermesAgent.agent_id == agent_id)
    owner = (await db.execute(stmt)).scalar_one_or_none()
    if owner is None or owner != request.user.id:
        # 不区分「不存在」与「他人持有」，避免泄漏 agent_id 是否存在
        raise errors.ForbiddenError(msg='无权查看该 Agent 用量')

    agent_usage_summary = await llm_newapi_user_mapping_service.get_usage_summary_by_agent(
        db, agent_id, start_time, end_time,
    )
    return response_base.success(data=agent_usage_summary)


@router.get(
    '/usage/detail',
    summary='查询用量明细（分页）',
    dependencies=[DependsJwtAuth],
)
async def get_newapi_usage_detail(
    request: Request,
    db: CurrentSession,
    start_time: Annotated[int | None, Query(description='开始时间 (unix timestamp)')] = None,
    end_time: Annotated[int | None, Query(description='结束时间 (unix timestamp)')] = None,
    model_name: Annotated[str | None, Query(description='模型名称筛选')] = None,
    limit: Annotated[int, Query(ge=1, le=200, description='每页条数')] = 50,
    offset: Annotated[int, Query(ge=0, description='偏移量')] = 0,
) -> ResponseSchemaModel[NewApiUsageDetail]:
    now = int(time.time())
    if not start_time:
        start_time = now - 30 * 86400
    if not end_time:
        end_time = now
    data = await llm_newapi_user_mapping_service.get_usage_detail(
        db, request.user.id, start_time, end_time,
        model_name=model_name, limit=limit, offset=offset,
    )
    return response_base.success(data=data)


@router.get(
    '/api-key',
    summary='获取用户的 new-api API Key',
    dependencies=[DependsJwtAuth],
)
async def get_newapi_key(request: Request, db: CurrentSession) -> ResponseSchemaModel:
    key = await llm_newapi_user_mapping_service.get_api_key(db, request.user.id)
    return response_base.success(data={'api_key': key})


@router.get(
    '/mapping',
    summary='获取/创建用户的 new-api 映射',
    dependencies=[DependsJwtAuth],
)
async def get_mapping_info(request: Request, db: CurrentSession) -> ResponseSchemaModel[NewApiMappingInfo]:
    nickname = getattr(request.user, 'nickname', '') or ''
    data = await llm_newapi_user_mapping_service.ensure_newapi_user(
        db, request.user.id, nickname=nickname,
    )
    return response_base.success(data=data)
