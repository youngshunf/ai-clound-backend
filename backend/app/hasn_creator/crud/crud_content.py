from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Content
from backend.app.hasn_creator.schema.content import CreateContentParam, UpdateContentParam


class CRUDContent(CRUDPlus[Content]):
    async def get(self, db: AsyncSession, pk: int) -> Content | None:
        """
        获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param pk: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Content]:
        """
        获取所有内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateContentParam) -> None:
        """
        创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param obj: 创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateContentParam) -> int:
        """
        更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param pk: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID
        :param obj: 更新 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param pks: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


content_dao: CRUDContent = CRUDContent(Content)
