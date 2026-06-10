from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.publish.model import Site
from backend.app.publish.schema.site import CreateSiteParam, UpdateSiteParam


class CRUDSite(CRUDPlus[Site]):
    async def get(self, db: AsyncSession, pk: int) -> Site | None:
        """
        获取已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）

        :param db: 数据库会话
        :param pk: 已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Site]:
        """
        获取所有已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateSiteParam) -> None:
        """
        创建已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）

        :param db: 数据库会话
        :param obj: 创建已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateSiteParam) -> int:
        """
        更新已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）

        :param db: 数据库会话
        :param pk: 已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针） ID
        :param obj: 更新 已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）

        :param db: 数据库会话
        :param pks: 已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


site_dao: CRUDSite = CRUDSite(Site)
