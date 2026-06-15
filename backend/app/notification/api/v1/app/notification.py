"""统一通知 用户端 API

路由前缀: /api/v1/notifications/app
认证方式: Owner JWT

通知中心是跨来源统一聚合的权威视图（D1）。覆盖全 category 的列表/未读/已读 + 主人偏好。

注意：FastAPI 端点签名里的 CurrentSession/PreferenceUpdate 等需在运行时解析依赖，故本文件
不使用 `from __future__ import annotations`，所有依赖类型必须运行时可见（与社区 API 同惯例）。
"""
from fastapi import APIRouter, Request

from backend.app.notification.schema.notification import PreferenceUpdate
from backend.app.notification.service.notification_service import notification_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _require_owner_hasn_id(db, user_id: int) -> str:
    """解析当前 Owner 的 human hasn_id（不存在则 404）。"""
    from backend.app.hasn_core import hasn_humans_dao
    from backend.common.exception import errors

    human = await hasn_humans_dao.get_by_user_id(db, user_id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


def _split_csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(',') if v.strip()] if value else None


@router.get(
    '/notifications',
    name='notif_app_list',
    summary='通知中心列表',
    description='当前用户全 category 通知（category/type/unread_only 过滤 + 游标分页 + 读时聚合）',
    dependencies=[DependsJwtAuth],
)
async def list_notifications(
    request: Request,
    db: CurrentSession,
    category: str | None = None,
    type: str | None = None,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    result = await notification_service.list_notifications(
        db,
        recipient_hasn_id=hasn_id,
        categories=_split_csv(category),
        types=_split_csv(type),
        unread_only=unread_only,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get(
    '/notifications/unread-count',
    name='notif_app_unread_count',
    summary='未读通知数（含按 type/category 分组）',
    dependencies=[DependsJwtAuth],
)
async def unread_count(request: Request, db: CurrentSession) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    result = await notification_service.unread_count(db, recipient_hasn_id=hasn_id)
    return response_base.success(data=result)


@router.put(
    '/notifications/read-all',
    name='notif_app_read_all',
    summary='全部已读（可按 category/type 过滤）',
    dependencies=[DependsJwtAuth],
)
async def read_all(
    request: Request,
    db: CurrentSessionTransaction,
    category: str | None = None,
    type: str | None = None,
) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    affected = await notification_service.mark_all_read(
        db, recipient_hasn_id=hasn_id, types=_split_csv(type), categories=_split_csv(category)
    )
    return response_base.success(data={'affected': affected})


@router.put(
    '/notifications/{notification_id}/read',
    name='notif_app_mark_read',
    summary='标记单条已读',
    dependencies=[DependsJwtAuth],
)
async def mark_read(
    request: Request,
    db: CurrentSessionTransaction,
    notification_id: int,
) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    await notification_service.mark_read(db, recipient_hasn_id=hasn_id, notification_id=notification_id)
    return response_base.success()


# ==================== 主人偏好（免打扰 + 分类渠道开关，D4） ====================


@router.get(
    '/notifications/preferences',
    name='notif_app_list_preferences',
    summary='获取通知偏好',
    dependencies=[DependsJwtAuth],
)
async def list_preferences(request: Request, db: CurrentSession) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    result = await notification_service.list_preferences(db, owner_id=hasn_id)
    return response_base.success(data={'items': result})


@router.put(
    '/notifications/preferences',
    name='notif_app_upsert_preference',
    summary='更新通知偏好（按 category，或 * 全局默认）',
    dependencies=[DependsJwtAuth],
)
async def upsert_preference(
    request: Request,
    db: CurrentSessionTransaction,
    body: PreferenceUpdate,
) -> ResponseModel:
    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    result = await notification_service.upsert_preference(
        db,
        owner_id=hasn_id,
        category=body.category,
        channels=body.channels.model_dump() if body.channels is not None else None,
        dnd=body.dnd.model_dump() if body.dnd is not None else None,
    )
    return response_base.success(data=result)


# ==================== 服务号身份（解析服务号会话名称/头像，§4.5） ====================


@router.get(
    '/service-accounts',
    name='notif_app_list_service_accounts',
    summary='获取本人名下的服务号（供消息列表解析服务号会话身份）',
    dependencies=[DependsJwtAuth],
)
async def list_service_accounts(request: Request, db: CurrentSession) -> ResponseModel:
    from backend.app.notification.service.service_account_service import service_account_service

    hasn_id = await _require_owner_hasn_id(db, request.user.id)
    items = await service_account_service.list_for_owner(db, owner_id=hasn_id)
    return response_base.success(data={'items': items})
