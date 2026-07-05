from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_release.model import AppRelease
from backend.app.hasn_release.schema.app_release import CreateAppReleaseParam, UpdateAppReleaseParam


class CRUDAppRelease(CRUDPlus[AppRelease]):
    async def get(self, db: AsyncSession, pk: int) -> AppRelease | None:
        """
        获取桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）

        :param db: 数据库会话
        :param pk: 桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[AppRelease]:
        """
        获取所有桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateAppReleaseParam) -> None:
        """
        创建桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）

        :param db: 数据库会话
        :param obj: 创建桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateAppReleaseParam) -> int:
        """
        更新桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）

        :param db: 数据库会话
        :param pk: 桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针） ID
        :param obj: 更新 桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针）

        :param db: 数据库会话
        :param pks: 桌面端发布版本（版本级·携 changelog + 状态 + is_latest 指针） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


app_release_dao: CRUDAppRelease = CRUDAppRelease(AppRelease)
