"""平台账号服务层"""

from typing import List, Sequence

from fastapi import Request
from sqlalchemy import Select

from backend.app.project.crud.crud_platform_account import platform_account_dao
from backend.app.project.model import PlatformAccount
from backend.app.project.schema.platform_account import (
    PlatformAccountCreate,
    PlatformAccountSync,
    PlatformAccountUpdate,
)
from backend.common.exception import errors
from backend.database.db import async_db_session


class PlatformAccountService:
    @staticmethod
    async def get(pk: int) -> PlatformAccount:
        async with async_db_session() as db:
            account = await platform_account_dao.get(db, pk)
            if not account:
                raise errors.NotFoundError(msg='平台账号不存在')
            return account

    @staticmethod
    async def list(project_id: int) -> Sequence[PlatformAccount]:
        async with async_db_session() as db:
            select_stmt = await platform_account_dao.get_list_by_project(db, project_id)
            return await platform_account_dao.select_all(db, select_stmt)

    @staticmethod
    async def list_by_user(user_id: int) -> Sequence[PlatformAccount]:
        """获取用户的所有平台账号（跨项目）"""
        async with async_db_session() as db:
            select_stmt = await platform_account_dao.get_list_by_user(db, user_id)
            return await platform_account_dao.select_all(db, select_stmt)

    @staticmethod
    async def create(request: Request, obj: PlatformAccountCreate) -> PlatformAccount:
        async with async_db_session.begin() as db:
            # 检查是否已存在
            existing = await platform_account_dao.get_by_project_and_id(
                db, obj.project_id, obj.account_id, obj.platform
            )
            if existing:
                raise errors.ForbiddenError(msg='该账号已添加到项目中')
                
            return await platform_account_dao.create(db, obj, request.user.id)

    @staticmethod
    async def update(pk: int, obj: PlatformAccountUpdate) -> int:
        async with async_db_session.begin() as db:
            account = await platform_account_dao.get(db, pk)
            if not account:
                raise errors.NotFoundError(msg='平台账号不存在')
            return await platform_account_dao.update(db, pk, obj)

    @staticmethod
    async def delete(pk: int) -> int:
        async with async_db_session.begin() as db:
            account = await platform_account_dao.get(db, pk)
            if not account:
                raise errors.NotFoundError(msg='平台账号不存在')
            return await platform_account_dao.soft_delete(db, pk)

    @staticmethod
    async def sync(request: Request, obj: PlatformAccountSync) -> PlatformAccount:
        """同步接口：接收客户端推送的账号信息（新建或更新）"""
        async with async_db_session.begin() as db:
            data = obj.model_dump()
            return await platform_account_dao.sync_upsert(
                db, 
                project_id=obj.project_id, 
                user_id=request.user.id, 
                data=data
            )
