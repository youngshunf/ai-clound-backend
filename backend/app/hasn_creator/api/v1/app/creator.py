"""创作运营用户端（owner）业务 API（设计 00 §6 + 91 §M8）。

认证：Owner JWT。主人在 WebUI 操作创作运营全链路——建项目/设画像/加账号竞品/补选题/建内容/
备稿/提交发布/审核批准/标记已发/回填数据/取成品包/查爆款/复盘/总览。**WebUI 经 daemon
`/api/v1/creator/*` 薄代理调用本面**（铁律：WebUI 不直连云端）。

定位：owner 面 = 业务操作（包裹 creator_service，CreatorScope 企业化裁剪），不是 codegen 裸 CRUD；
Agent 面（`hasn.creator.*` 工具）走云端 MCP（gateway_internal handler），不经本面。审核批准/标记已发
为「人」专属动作（approve_publish/mark_published），分身工具面不暴露。

身份恒取自 Owner JWT（request.user.id）；scope 经 resolve_creator_scope（owner_hasn_id 由 user_id 解析，
active_enterprise → 角色裁剪）。一律返回统一信封（ResponseModel + response_base.success）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.app.hasn_creator.schema.owner import (
    AddAccountParam,
    CreateContentParam,
    CreateProjectParam,
    LogCompetitorParam,
    LogInsightParam,
    MarkPublishedParam,
    SaveStageParam,
    SetProfileParam,
    SubmitPublishParam,
    SuggestTopicsParam,
    UpdateContentParam,
    UpdateMetricsParam,
    UpdateProjectParam,
)
from backend.app.hasn_creator.service.creator_service import creator_service
from backend.app.hasn_creator.service.scope_context import resolve_creator_scope
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


# ============================ 项目 / 画像 ============================


@router.get('/projects', summary='[Owner] 项目（号）列表', dependencies=[DependsJwtAuth])
async def list_projects(
    request: Request,
    db: CurrentSession,
    status: str | None = Query(default=None),
    view: str = Query(default='team', description='企业内主编可切 mine'),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id, view=view)
    items = await creator_service.list_projects(db, user_id=request.user.id, scope=scope, status=status, limit=limit)
    return response_base.success(data={'items': items, 'scope': scope.to_meta()})


@router.post('/projects', summary='[Owner] 新建项目（号）', dependencies=[DependsJwtAuth])
async def create_project(request: Request, db: CurrentSessionTransaction, obj: CreateProjectParam) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.create_project(
        db,
        user_id=request.user.id,
        scope=scope,
        name=obj.name,
        description=obj.description,
        primary_platform=obj.primary_platform,
        pipeline_mode=obj.pipeline_mode,
        playbook_id=obj.playbook_id,
        assignee_agent_id=obj.assignee_agent_id,
    )
    return response_base.success(data=data)


@router.get('/projects/{project_id}', summary='[Owner] 项目详情（含画像/账号/计数）', dependencies=[DependsJwtAuth])
async def get_project(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.get_project(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data=data)


@router.patch('/projects/{project_id}', summary='[Owner] 更新项目', dependencies=[DependsJwtAuth])
async def update_project(
    request: Request, db: CurrentSessionTransaction, project_id: int, obj: UpdateProjectParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.update_project(
        db,
        user_id=request.user.id,
        scope=scope,
        project_id=project_id,
        fields=obj.model_dump(exclude_none=True),
    )
    return response_base.success(data=data)


@router.get('/projects/{project_id}/profile', summary='[Owner] 取项目画像', dependencies=[DependsJwtAuth])
async def get_profile(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.get_profile(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data=data)


@router.put('/projects/{project_id}/profile', summary='[Owner] 设置项目画像', dependencies=[DependsJwtAuth])
async def set_profile(
    request: Request, db: CurrentSessionTransaction, project_id: int, obj: SetProfileParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.set_profile(
        db, user_id=request.user.id, scope=scope, project_id=project_id, fields=obj.fields
    )
    return response_base.success(data=data)


@router.post('/projects/{project_id}/profile/analyze', summary='[Owner] 触发画像分析', dependencies=[DependsJwtAuth])
async def analyze_profile(request: Request, db: CurrentSessionTransaction, project_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.analyze_profile(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data=data)


# ============================ 账号 / 竞品 / 选题 ============================


@router.get('/projects/{project_id}/accounts', summary='[Owner] 项目账号列表', dependencies=[DependsJwtAuth])
async def list_accounts(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.list_accounts(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data={'items': items})


@router.post('/projects/{project_id}/accounts', summary='[Owner] 加平台账号', dependencies=[DependsJwtAuth])
async def add_account(
    request: Request, db: CurrentSessionTransaction, project_id: int, obj: AddAccountParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.add_account(
        db, user_id=request.user.id, scope=scope, project_id=project_id, platform=obj.platform, fields=obj.fields
    )
    return response_base.success(data=data)


@router.get('/projects/{project_id}/competitors', summary='[Owner] 竞品列表', dependencies=[DependsJwtAuth])
async def list_competitors(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.list_competitors(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data={'items': items})


@router.post('/projects/{project_id}/competitors', summary='[Owner] 记录竞品', dependencies=[DependsJwtAuth])
async def log_competitor(
    request: Request, db: CurrentSessionTransaction, project_id: int, obj: LogCompetitorParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.log_competitor(
        db, user_id=request.user.id, scope=scope, project_id=project_id, name=obj.name, fields=obj.fields
    )
    return response_base.success(data=data)


@router.get('/projects/{project_id}/topics', summary='[Owner] 选题池', dependencies=[DependsJwtAuth])
async def list_topics(
    request: Request,
    db: CurrentSession,
    project_id: int,
    status: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.list_topics(
        db, user_id=request.user.id, scope=scope, project_id=project_id, status=status, limit=limit
    )
    return response_base.success(data={'items': items})


@router.post('/projects/{project_id}/topics', summary='[Owner] 补充选题', dependencies=[DependsJwtAuth])
async def suggest_topics(
    request: Request, db: CurrentSessionTransaction, project_id: int, obj: SuggestTopicsParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.suggest_topics(
        db, user_id=request.user.id, scope=scope, project_id=project_id, topics=obj.topics, batch_date=obj.batch_date
    )
    return response_base.success(data={'items': items})


# ============================ 内容 / 阶段产出 ============================


@router.get('/contents', summary='[Owner] 内容列表', dependencies=[DependsJwtAuth])
async def list_content(
    request: Request,
    db: CurrentSession,
    project_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    view: str = Query(default='team'),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id, view=view)
    items = await creator_service.list_content(
        db,
        user_id=request.user.id,
        scope=scope,
        project_id=project_id,
        status=status,
        review_status=review_status,
        limit=limit,
    )
    return response_base.success(data={'items': items, 'scope': scope.to_meta()})


@router.post('/contents', summary='[Owner] 新建内容', dependencies=[DependsJwtAuth])
async def create_content(request: Request, db: CurrentSessionTransaction, obj: CreateContentParam) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.create_content(
        db,
        user_id=request.user.id,
        scope=scope,
        project_id=obj.project_id,
        title=obj.title,
        content_tracks=obj.content_tracks,
        target_platforms=obj.target_platforms,
        topic_id=obj.topic_id,
        viral_pattern_id=obj.viral_pattern_id,
        playbook_id=obj.playbook_id,
        pipeline_mode=obj.pipeline_mode,
    )
    return response_base.success(data=data)


@router.get('/contents/{content_id}', summary='[Owner] 内容详情（含阶段产出）', dependencies=[DependsJwtAuth])
async def get_content(request: Request, db: CurrentSession, content_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.get_content(db, user_id=request.user.id, scope=scope, content_id=content_id)
    return response_base.success(data=data)


@router.patch('/contents/{content_id}', summary='[Owner] 更新内容（状态机/审核）', dependencies=[DependsJwtAuth])
async def update_content(
    request: Request, db: CurrentSessionTransaction, content_id: int, obj: UpdateContentParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    # 审核动作由主人执行 → reviewer_user_id 取当前主人。
    data = await creator_service.update_content(
        db,
        user_id=request.user.id,
        scope=scope,
        content_id=content_id,
        status=obj.status,
        title=obj.title,
        review_status=obj.review_status,
        review_note=obj.review_note,
        reviewer_user_id=request.user.id if obj.review_status else None,
        metadata=obj.metadata,
    )
    return response_base.success(data=data)


@router.post('/contents/{content_id}/stages', summary='[Owner] 保存阶段产出', dependencies=[DependsJwtAuth])
async def save_stage(
    request: Request, db: CurrentSessionTransaction, content_id: int, obj: SaveStageParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.save_stage(
        db,
        user_id=request.user.id,
        scope=scope,
        content_id=content_id,
        stage=obj.stage,
        content_text=obj.content_text,
        asset_refs=obj.asset_refs,
        source_type=obj.source_type,
    )
    return response_base.success(data=data)


# ============================ 发布（manual_assist 主路径） ============================


@router.get('/publishes', summary='[Owner] 发布记录列表', dependencies=[DependsJwtAuth])
async def list_publish(
    request: Request,
    db: CurrentSession,
    project_id: int | None = Query(default=None),
    content_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.list_publish(
        db,
        user_id=request.user.id,
        scope=scope,
        project_id=project_id,
        content_id=content_id,
        status=status,
        limit=limit,
    )
    return response_base.success(data={'items': items})


@router.post('/publishes', summary='[Owner] 提交发布（→待审）', dependencies=[DependsJwtAuth])
async def submit_publish(request: Request, db: CurrentSessionTransaction, obj: SubmitPublishParam) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.submit_publish(
        db,
        user_id=request.user.id,
        scope=scope,
        content_id=obj.content_id,
        account_id=obj.account_id,
        platform=obj.platform,
        method=obj.method,
        publish_note=obj.publish_note,
    )
    return response_base.success(data=data)


@router.get('/publishes/{publish_id}/package', summary='[Owner] 取成品包（文案+封面+话题+建议）', dependencies=[DependsJwtAuth])
async def get_publish_package(request: Request, db: CurrentSession, publish_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.assemble_publish_package(
        db, user_id=request.user.id, scope=scope, publish_id=publish_id
    )
    return response_base.success(data=data)


@router.post('/publishes/{publish_id}/approve', summary='[Owner] 批准发布（人专属）', dependencies=[DependsJwtAuth])
async def approve_publish(request: Request, db: CurrentSessionTransaction, publish_id: int) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.approve_publish(
        db, user_id=request.user.id, scope=scope, publish_id=publish_id, approval_user_id=request.user.id
    )
    return response_base.success(data=data)


@router.post('/publishes/{publish_id}/mark-published', summary='[Owner] 标记已发布并回填链接（人专属）', dependencies=[DependsJwtAuth])
async def mark_published(
    request: Request, db: CurrentSessionTransaction, publish_id: int, obj: MarkPublishedParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.mark_published(
        db, user_id=request.user.id, scope=scope, publish_id=publish_id, publish_url=obj.publish_url
    )
    return response_base.success(data=data)


@router.post('/publishes/{publish_id}/metrics', summary='[Owner] 回填发布数据指标', dependencies=[DependsJwtAuth])
async def update_metrics(
    request: Request, db: CurrentSessionTransaction, publish_id: int, obj: UpdateMetricsParam
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.update_metrics(
        db, user_id=request.user.id, scope=scope, publish_id=publish_id, metrics=obj.metrics
    )
    return response_base.success(data=data)


# ============================ 爆款 / 复盘 / 总览 ============================


@router.get('/patterns', summary='[Owner] 爆款模式检索', dependencies=[DependsJwtAuth])
async def search_patterns(
    request: Request,
    db: CurrentSession,
    project_id: int | None = Query(default=None),
    pattern_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    items = await creator_service.search_patterns(
        db, user_id=request.user.id, scope=scope, project_id=project_id, pattern_type=pattern_type, query=q, limit=limit
    )
    return response_base.success(data={'items': items})


@router.post('/insights', summary='[Owner] 记录复盘洞察（原子回写进化）', dependencies=[DependsJwtAuth])
async def log_insight(request: Request, db: CurrentSessionTransaction, obj: LogInsightParam) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id)
    data = await creator_service.log_insight(
        db,
        user_id=request.user.id,
        scope=scope,
        project_id=obj.project_id,
        insight_type=obj.insight_type,
        summary=obj.summary,
        period=obj.period,
        evidence_json=obj.evidence_json,
        proposed_action=obj.proposed_action,
        confidence=obj.confidence,
    )
    return response_base.success(data=data)


@router.get('/report/overview', summary='[Owner] 运营总览', dependencies=[DependsJwtAuth])
async def report_overview(
    request: Request,
    db: CurrentSession,
    project_id: int | None = Query(default=None),
    view: str = Query(default='team'),
) -> ResponseModel:
    scope = await resolve_creator_scope(db, user_id=request.user.id, view=view)
    data = await creator_service.report_overview(db, user_id=request.user.id, scope=scope, project_id=project_id)
    return response_base.success(data=data)
