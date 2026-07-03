from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnPendingIntents
from backend.app.hasn.schema.hasn_pending_intents import CreateHasnPendingIntentsParam, UpdateHasnPendingIntentsParam


class CRUDHasnPendingIntents(CRUDPlus[HasnPendingIntents]):
    async def get(self, db: AsyncSession, pk: int) -> HasnPendingIntents | None:
        """
        获取HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param pk: HASN 第三方渠道反向 onboarding pending intent  ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 第三方渠道反向 onboarding pending intent 列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnPendingIntents]:
        """
        获取所有HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnPendingIntentsParam) -> None:
        """
        创建HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param obj: 创建HASN 第三方渠道反向 onboarding pending intent 参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnPendingIntentsParam) -> int:
        """
        更新HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param pk: HASN 第三方渠道反向 onboarding pending intent  ID
        :param obj: 更新 HASN 第三方渠道反向 onboarding pending intent 参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 第三方渠道反向 onboarding pending intent

        :param db: 数据库会话
        :param pks: HASN 第三方渠道反向 onboarding pending intent  ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_pending_intents_dao: CRUDHasnPendingIntents = CRUDHasnPendingIntents(HasnPendingIntents)
