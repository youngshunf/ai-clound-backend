from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_deck.model import Deck
from backend.app.hasn_deck.schema.deck import CreateDeckParam, UpdateDeckParam


class CRUDDeck(CRUDPlus[Deck]):
    async def get(self, db: AsyncSession, pk: int) -> Deck | None:
        """
        获取演示文稿（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取演示文稿（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Deck]:
        """
        获取所有演示文稿（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateDeckParam) -> None:
        """
        创建演示文稿（云端权威）

        :param db: 数据库会话
        :param obj: 创建演示文稿（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateDeckParam) -> int:
        """
        更新演示文稿（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿（云端权威） ID
        :param obj: 更新 演示文稿（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除演示文稿（云端权威）

        :param db: 数据库会话
        :param pks: 演示文稿（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


deck_dao: CRUDDeck = CRUDDeck(Deck)
