"""平台项目（doc38）用户端（app-scope）API 公共依赖：owner 解析 + 写点 WSPUSH 失效。

owner 身份一律由 Owner JWT（``request.user.id``）解析出主人 hasn_id，**绝不读请求体身份**
（owner 隔离铁律，doc38 §3/§4）。写点后经 ``bump_owner(KIND_PROJECT)`` 给该 owner 在线节点
推 ``hasn.sync.invalidate(project)``（best-effort，对齐 plan/tasks）。

两个用户端 API 文件（``hasn_project.py`` / ``hasn_project_milestone.py``）共用这套 helper，避免各写一份。
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any

from backend.app.hasn_core import identity
from backend.common.exception import errors
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def resolve_owner_human(db: AsyncSession, request: Request) -> Any:
    """登录用户 → HASN 主人身份行（``HasnHumans``）。"""
    human = await identity.get_human_by_user_id(db, user_id=request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human


async def resolve_owner(db: AsyncSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id（owner 隔离键）。"""
    return (await resolve_owner_human(db, request)).hasn_id


async def bump_project_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """owner 写点后 → WSPUSH ``hasn.sync.invalidate(project)`` 给该 owner 在线节点（best-effort）。

    project 是 owner 定向 kind（doc38 U5，对齐 tasks/plan）：走 ``bump_owner`` 仅推该 owner 的在线
    节点，revision 是 per-owner 维度（不进全局握手）。push 失败**不抛**——离线设备靠重连握手 +
    周期对账追平，写点绝不能因推送失败而失败。
    """
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        await siv.bump_owner(siv.KIND_PROJECT, db, owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[project] sync invalidate 推送失败 (非致命): %s', e)


async def bump_task_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """项目巡检任务变更后通知 owner 节点拉取既有任务镜像（best-effort）。"""
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        await siv.bump_owner(siv.KIND_TASKS, db, owner_hasn_id)
    except Exception as e:
        log.warning('[project] tasks sync invalidate 推送失败 (非致命): %s', e)


async def bump_linked_resource_sync(
    db: AsyncSession,
    *,
    owner_hasn_id: str,
    resource_uri: str,
) -> None:
    """挂靠事务提交后发布资源域失效信号；失败只告警，不回滚已提交业务。"""
    try:
        from backend.app.hasn_project.service.project_linkage_registry import (
            project_linkage_registry,
        )

        await project_linkage_registry.bump_sync_after_commit(
            db,
            owner=owner_hasn_id,
            resource_uri=resource_uri,
        )
    except Exception as e:  # 推送 best-effort
        log.warning('[project] linked resource sync invalidate 推送失败 (非致命): %s', e)


async def publish_project_linkage_sync_after_commit(
    *,
    owner_hasn_id: str,
    resource_uri: str,
    resource_changed: bool,
) -> None:
    """挂靠事务提交后用独立 session 发布项目域与资源域失效。

    ``CurrentSessionTransaction`` 由 ``async_db_session.begin()`` 提供；调用方手动提交后不能继续
    复用该 session 做 revision 查询，因此这里新开只读 session，避免提交成功却静默漏发失效。
    """
    try:
        async with async_db_session() as sync_db:
            await bump_project_sync(sync_db, owner_hasn_id)
            if resource_changed:
                await bump_linked_resource_sync(
                    sync_db,
                    owner_hasn_id=owner_hasn_id,
                    resource_uri=resource_uri,
                )
    except Exception as e:  # 推送 best-effort
        log.warning('[project] post-commit sync invalidate 发布失败 (非致命): %s', e)
