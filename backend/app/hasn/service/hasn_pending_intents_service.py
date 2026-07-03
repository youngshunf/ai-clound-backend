from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_pending_intents import hasn_pending_intents_dao
from backend.app.hasn.model import HasnPendingIntents
from backend.app.hasn.schema.hasn_pending_intents import (
    CreateHasnPendingIntentsParam,
    DeleteHasnPendingIntentsParam,
    UpdateHasnPendingIntentsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnPendingIntentsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnPendingIntents:
        """
        获取HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param pk: HASN 第三方渠道反向 onboarding pending intent  ID
        :return:
        """
        hasn_pending_intents = await hasn_pending_intents_dao.get(db, pk)
        if not hasn_pending_intents:
            raise errors.NotFoundError(msg='HASN 第三方渠道反向 onboarding pending intent 不存在')
        return hasn_pending_intents

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 第三方渠道反向 onboarding pending intent 列表

        :param db: 数据库会话
        :return:
        """
        hasn_pending_intents_select = await hasn_pending_intents_dao.get_select()
        return await paging_data(db, hasn_pending_intents_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnPendingIntents]:
        """
        获取所有HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :return:
        """
        hasn_pending_intentss = await hasn_pending_intents_dao.get_all(db)
        return hasn_pending_intentss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnPendingIntentsParam) -> None:
        """
        创建HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param obj: 创建HASN 第三方渠道反向 onboarding pending intent 参数
        :return:
        """
        await hasn_pending_intents_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnPendingIntentsParam) -> int:
        """
        更新HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param pk: HASN 第三方渠道反向 onboarding pending intent  ID
        :param obj: 更新HASN 第三方渠道反向 onboarding pending intent 参数
        :return:
        """
        count = await hasn_pending_intents_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnPendingIntentsParam) -> int:
        """
        删除HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param obj: HASN 第三方渠道反向 onboarding pending intent  ID 列表
        :return:
        """
        count = await hasn_pending_intents_dao.delete(db, obj.pks)
        return count


hasn_pending_intents_service: HasnPendingIntentsService = HasnPendingIntentsService()
