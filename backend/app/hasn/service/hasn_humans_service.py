from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model import HasnHumans
from backend.app.hasn.schema.hasn_humans import CreateHasnHumansParam, DeleteHasnHumansParam, UpdateHasnHumansParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnHumansService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnHumans:
        """
        获取HASN 人类用户身份

        :param db: 数据库会话
        :param pk: HASN 人类用户身份 ID
        :return:
        """
        hasn_humans = await hasn_humans_dao.get(db, pk)
        if not hasn_humans:
            raise errors.NotFoundError(msg='HASN 人类用户身份不存在')
        return hasn_humans

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 人类用户身份列表

        :param db: 数据库会话
        :return:
        """
        hasn_humans_select = await hasn_humans_dao.get_select()
        return await paging_data(db, hasn_humans_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnHumans]:
        """
        获取所有HASN 人类用户身份

        :param db: 数据库会话
        :return:
        """
        hasn_humanss = await hasn_humans_dao.get_all(db)
        return hasn_humanss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnHumansParam) -> None:
        """
        创建HASN 人类用户身份

        :param db: 数据库会话
        :param obj: 创建HASN 人类用户身份参数
        :return:
        """
        await hasn_humans_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnHumansParam) -> int:
        """
        更新HASN 人类用户身份

        :param db: 数据库会话
        :param pk: HASN 人类用户身份 ID
        :param obj: 更新HASN 人类用户身份参数
        :return:
        """
        count = await hasn_humans_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnHumansParam) -> int:
        """
        删除HASN 人类用户身份

        :param db: 数据库会话
        :param obj: HASN 人类用户身份 ID 列表
        :return:
        """
        count = await hasn_humans_dao.delete(db, obj.pks)
        return count


hasn_humans_service: HasnHumansService = HasnHumansService()
