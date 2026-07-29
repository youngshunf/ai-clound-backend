from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import HasnDocSpaceSubscriptions
from backend.app.hasn_community.schema.hasn_doc_space_subscriptions import (
    CreateHasnDocSpaceSubscriptionsParam,
    UpdateHasnDocSpaceSubscriptionsParam,
)


class CRUDHasnDocSpaceSubscriptions(CRUDPlus[HasnDocSpaceSubscriptions]):
    async def get(self, db: AsyncSession, pk: int) -> HasnDocSpaceSubscriptions | None:
        """按内部主键获取社区文集订阅关系。"""
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取社区文集订阅关系列表查询表达式。"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnDocSpaceSubscriptions]:
        """获取全部社区文集订阅关系。"""
        return await self.select_models(db)

    async def create(
        self,
        db: AsyncSession,
        obj: CreateHasnDocSpaceSubscriptionsParam,
    ) -> None:
        """创建社区文集订阅关系。"""
        await self.create_model(db, obj)

    async def update(
        self,
        db: AsyncSession,
        pk: int,
        obj: UpdateHasnDocSpaceSubscriptionsParam,
    ) -> int:
        """更新社区文集订阅关系。"""
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """批量删除社区文集订阅关系。"""
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_doc_space_subscriptions_dao: CRUDHasnDocSpaceSubscriptions = (
    CRUDHasnDocSpaceSubscriptions(HasnDocSpaceSubscriptions)
)
