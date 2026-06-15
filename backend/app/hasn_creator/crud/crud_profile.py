from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Profile
from backend.app.hasn_creator.schema.profile import CreateProfileParam, UpdateProfileParam


class CRUDProfile(CRUDPlus[Profile]):
    async def get(self, db: AsyncSession, pk: int) -> Profile | None:
        """
        获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param pk: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Profile]:
        """
        获取所有项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateProfileParam) -> None:
        """
        创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param obj: 创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateProfileParam) -> int:
        """
        更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param pk: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID
        :param obj: 更新 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param pks: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


profile_dao: CRUDProfile = CRUDProfile(Profile)
