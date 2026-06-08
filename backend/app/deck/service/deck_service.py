from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.deck.crud.crud_deck import deck_dao
from backend.app.deck.model import Deck
from backend.app.deck.schema.deck import CreateDeckParam, DeleteDeckParam, UpdateDeckParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class DeckService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Deck:
        """
        获取演示文稿（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿（云端权威） ID
        :return:
        """
        deck = await deck_dao.get(db, pk)
        if not deck:
            raise errors.NotFoundError(msg='演示文稿（云端权威）不存在')
        return deck

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取演示文稿（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        deck_select = await deck_dao.get_select()
        return await paging_data(db, deck_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Deck]:
        """
        获取所有演示文稿（云端权威）

        :param db: 数据库会话
        :return:
        """
        deck_list = await deck_dao.get_all(db)
        return deck_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateDeckParam) -> None:
        """
        创建演示文稿（云端权威）

        :param db: 数据库会话
        :param obj: 创建演示文稿（云端权威）参数
        :return:
        """
        await deck_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateDeckParam) -> int:
        """
        更新演示文稿（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿（云端权威） ID
        :param obj: 更新演示文稿（云端权威）参数
        :return:
        """
        count = await deck_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteDeckParam) -> int:
        """
        删除演示文稿（云端权威）

        :param db: 数据库会话
        :param obj: 演示文稿（云端权威） ID 列表
        :return:
        """
        count = await deck_dao.delete(db, obj.pks)
        return count


deck_service: DeckService = DeckService()
