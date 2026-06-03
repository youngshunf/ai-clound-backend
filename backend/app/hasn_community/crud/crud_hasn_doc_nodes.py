from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import HasnDocNodes
from backend.app.hasn_community.schema.hasn_doc_nodes import CreateHasnDocNodesParam, UpdateHasnDocNodesParam


class CRUDHasnDocNodes(CRUDPlus[HasnDocNodes]):
    async def get(self, db: AsyncSession, pk: int) -> HasnDocNodes | None:
        """
        获取文集多级目录树节点

        :param db: 数据库会话
        :param pk: 文集多级目录树节点 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取文集多级目录树节点列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnDocNodes]:
        """
        获取所有文集多级目录树节点

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnDocNodesParam) -> None:
        """
        创建文集多级目录树节点

        :param db: 数据库会话
        :param obj: 创建文集多级目录树节点参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnDocNodesParam) -> int:
        """
        更新文集多级目录树节点

        :param db: 数据库会话
        :param pk: 文集多级目录树节点 ID
        :param obj: 更新 文集多级目录树节点参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除文集多级目录树节点

        :param db: 数据库会话
        :param pks: 文集多级目录树节点 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_doc_nodes_dao: CRUDHasnDocNodes = CRUDHasnDocNodes(HasnDocNodes)
