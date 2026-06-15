from __future__ import annotations

import sqlalchemy as sa

from fastapi import APIRouter, Request

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_core.app_platform import (
    BuiltinTaskCatalogResponse,
    GetHasnAppEntitlementDetail,
    InstanceResolutionError,
    app_catalog_service,
    workbench_domain_service,
)
from backend.app.hasn_task.service.builtin_task_service import workbench_builtin_task_service
from backend.app.workbench.schema.hasn_owner_workbench_pref import PutWorkbenchPrefParam, WorkbenchPrefResponse
from backend.app.workbench.schema.workbench_briefing_document import (
    BriefingDismissParam,
    BriefingHistoryResponse,
    BriefingLatestResponse,
)
from backend.app.workbench.service.hasn_workbench_briefing_feedback_service import (
    hasn_workbench_briefing_feedback_service,
)
from backend.app.workbench.service.hasn_workbench_briefing_service import hasn_workbench_briefing_service
from backend.app.workbench.service.workbench_pref_service import workbench_pref_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction  # noqa: TC001

router = APIRouter()


async def _resolve_owner_id(request: Request, db: CurrentSession) -> str:
    """当前登录用户 → hasn_humans.hasn_id（owner 身份）。"""
    owner = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == request.user.id).limit(1))
    ).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner


@router.get('/workbench/pref', dependencies=[DependsJwtAuth], summary='读工作台偏好（主脑 + 简报设置）')
async def get_workbench_pref(request: Request, db: CurrentSession) -> ResponseSchemaModel[WorkbenchPrefResponse]:
    owner_id = await _resolve_owner_id(request, db)
    data = await workbench_pref_service.get_or_init_pref(db, owner_id)
    return response_base.success(data=data)


@router.put('/workbench/pref', dependencies=[DependsJwtAuth], summary='改工作台偏好（主脑 / 简报开关·时刻·数据源）')
async def update_workbench_pref(
    request: Request, db: CurrentSessionTransaction, obj: PutWorkbenchPrefParam
) -> ResponseSchemaModel[WorkbenchPrefResponse]:
    owner_id = await _resolve_owner_id(request, db)
    data = await workbench_pref_service.update_pref(
        db,
        owner_id,
        primary_agent_id=obj.primary_agent_id,
        briefing_enabled=obj.briefing_enabled,
        briefing_time=obj.briefing_time,
        briefing_sources=obj.briefing_sources,
    )
    return response_base.success(data=data)


@router.get('/workbench/builtin-tasks', dependencies=[DependsJwtAuth], summary='读官方内置任务目录（daemon 拉取播种）')
async def list_builtin_tasks(db: CurrentSession) -> ResponseSchemaModel[BuiltinTaskCatalogResponse]:
    data = await workbench_builtin_task_service.list_enabled(db)
    return response_base.success(data=data)


@router.get('/workbench/briefing/latest', dependencies=[DependsJwtAuth], summary='读当日/指定日简报（owner 隔离，今日视图过滤已忽略项）')
async def get_briefing_latest(
    request: Request, db: CurrentSession, period: str | None = None, include_dismissed: bool = False
) -> ResponseSchemaModel[BriefingLatestResponse]:
    owner_id = await _resolve_owner_id(request, db)
    row = await hasn_workbench_briefing_service.get_latest(db=db, owner_hasn_id=owner_id, period=period)
    if row is None:
        # 空态如实返回（前端引导「立即生成」，绝不造卡）。
        return response_base.success(data=BriefingLatestResponse(has_briefing=False))
    document = row.document_json or {}
    # 读已忽略键：今日实时视图据此过滤（刷新不再出现 = dismiss 持久化）；
    # 历史视图（include_dismissed=true）返回完整文档 + dismissed_refs 供前端标「已忽略」。
    item_ids, source_refs = await hasn_workbench_briefing_feedback_service.dismissed_keys(
        db=db, owner_hasn_id=owner_id, period=row.period
    )
    dismissed_refs = sorted(item_ids | source_refs)
    if not include_dismissed and dismissed_refs:
        document = hasn_workbench_briefing_service.filter_dismissed(document, item_ids, source_refs)
    return response_base.success(
        data=BriefingLatestResponse(
            has_briefing=True,
            state=row.state,
            period=row.period,
            generated_at=document.get('generated_at'),
            document=document,
            dismissed_refs=dismissed_refs,
        )
    )


@router.get('/workbench/briefing/history', dependencies=[DependsJwtAuth], summary='历史简报列表（按日倒序，归档往期）')
async def list_briefing_history(
    request: Request, db: CurrentSession, limit: int = 60
) -> ResponseSchemaModel[BriefingHistoryResponse]:
    owner_id = await _resolve_owner_id(request, db)
    items = await hasn_workbench_briefing_service.get_history(
        db=db, owner_hasn_id=owner_id, limit=max(1, min(limit, 180))
    )
    return response_base.success(data=BriefingHistoryResponse(items=items))


@router.post(
    '/workbench/briefing/items/{item_id}/dismiss',
    dependencies=[DependsJwtAuth],
    summary='标记关注项已处理（反馈闭环，喂下次去重/降权）',
)
async def dismiss_briefing_item(
    request: Request, db: CurrentSessionTransaction, item_id: str, obj: BriefingDismissParam
) -> ResponseModel:
    owner_id = await _resolve_owner_id(request, db)
    await hasn_workbench_briefing_feedback_service.record(
        db=db,
        owner_hasn_id=owner_id,
        period=obj.period,
        item_id=item_id,
        action=obj.action,
        source_ref=obj.source_ref,
        note=obj.note,
    )
    return response_base.success(data={'item_id': item_id, 'action': obj.action})


# 应用平台 v3 P3（设计 17 决策①）：当前空间「已挂载应用」端点已删除——应用一律开箱即用，
# 工作台展示统一走下面的 GET /workbench/apps（catalog ∩ entitlement）。


@router.get('/workbench/apps', dependencies=[DependsJwtAuth], summary='工作台全部已注册应用（注册即用）')
async def list_workbench_apps(request: Request, db: CurrentSession, workspace_kind: str | None = None) -> ResponseModel:
    apps = await workbench_domain_service.list_workbench_apps(
        db,
        user_id=request.user.id,
        workspace_kind=workspace_kind,
    )
    return response_base.success(data=apps)


@router.get(
    '/workbench/apps/{app_id}/entry',
    dependencies=[DependsJwtAuth],
    summary='解析应用入口句柄（按当前空间解析实例，凭据不下发浏览器）',
)
async def resolve_workbench_app_entry(request: Request, db: CurrentSession, app_id: str) -> ResponseModel:
    try:
        handle = await workbench_domain_service.resolve_app_entry(db, user_id=request.user.id, app_id=app_id)
    except InstanceResolutionError as exc:
        # 实例未配置 / 凭据无效 / transport 不允许此面 —— 如实透出错误码（设计 11 §11）。
        raise errors.ServerError(msg=exc.message, data={'code': exc.code}) from exc
    return response_base.success(data=handle)


# 应用平台 v3 P3（设计 17 决策①）：挂载/卸载端点（POST/DELETE current/apps/{app_id}）已删除——
# 应用开箱即用，无挂载开关；付费墙在 GET /workbench/apps 的 access 字段 + invoke 时把关。


# ============================ C5：付费应用 试用 / 我的权益（owner 维度） ============================


@router.post(
    '/workbench/apps/{app_id}/trial',
    dependencies=[DependsJwtAuth],
    summary='开通付费应用试用（每个 app 仅一次，写 owner 维度试用权益）',
)
async def open_app_trial(request: Request, db: CurrentSessionTransaction, app_id: str) -> ResponseModel:
    owner_id = await _resolve_owner_id(request, db)
    catalog = await app_catalog_service.get_catalog(db, app_id=app_id)
    if catalog is None:
        raise errors.NotFoundError(msg='应用不存在')
    # open_trial 内含校验（published + 付费 + trial_days>0 + 未用过 + 无 active 权益），违反抛 4xx。
    ent = await app_catalog_service.open_trial(db, catalog=catalog, owner_hasn_id=owner_id)
    return response_base.success(data=GetHasnAppEntitlementDetail.model_validate(ent))


@router.get(
    '/workbench/entitlements',
    dependencies=[DependsJwtAuth],
    summary='我的应用权益（owner 维度，含试用/购买/管理员授予）',
)
async def list_my_entitlements(
    request: Request, db: CurrentSession, active_only: bool = False
) -> ResponseModel:
    owner_id = await _resolve_owner_id(request, db)
    rows = await app_catalog_service.list_entitlements(
        db, subject_type='owner', subject_id=owner_id, active_only=active_only
    )
    return response_base.success(data=[GetHasnAppEntitlementDetail.model_validate(r) for r in rows])
