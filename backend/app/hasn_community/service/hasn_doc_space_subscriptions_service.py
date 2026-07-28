from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_community.crud.crud_hasn_doc_space_subscriptions import (
    hasn_doc_space_subscriptions_dao,
)
from backend.app.hasn_community.model import HasnDocSpaceSubscriptions
from backend.app.hasn_community.schema.hasn_doc_space_subscriptions import (
    CreateHasnDocSpaceSubscriptionsParam,
    DeleteHasnDocSpaceSubscriptionsParam,
    UpdateHasnDocSpaceSubscriptionsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnDocSpaceSubscriptionsService:
    """codegen 生成的文集订阅基础服务。"""

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnDocSpaceSubscriptions:
        """按内部主键获取文集订阅关系。"""
        subscription = await hasn_doc_space_subscriptions_dao.get(db, pk)
        if not subscription:
            raise errors.NotFoundError(msg='社区文集订阅关系不存在')
        return subscription

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """获取文集订阅关系列表。"""
        subscription_select = await hasn_doc_space_subscriptions_dao.get_select()
        return await paging_data(db, subscription_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnDocSpaceSubscriptions]:
        """获取全部文集订阅关系。"""
        return await hasn_doc_space_subscriptions_dao.get_all(db)

    @staticmethod
    async def create(
        *,
        db: AsyncSession,
        obj: CreateHasnDocSpaceSubscriptionsParam,
    ) -> None:
        """创建文集订阅关系。"""
        await hasn_doc_space_subscriptions_dao.create(db, obj)

    @staticmethod
    async def update(
        *,
        db: AsyncSession,
        pk: int,
        obj: UpdateHasnDocSpaceSubscriptionsParam,
    ) -> int:
        """更新文集订阅关系。"""
        return await hasn_doc_space_subscriptions_dao.update(db, pk, obj)

    @staticmethod
    async def delete(
        *,
        db: AsyncSession,
        obj: DeleteHasnDocSpaceSubscriptionsParam,
    ) -> int:
        """删除文集订阅关系。"""
        return await hasn_doc_space_subscriptions_dao.delete(db, obj.pks)


hasn_doc_space_subscriptions_service = HasnDocSpaceSubscriptionsService()
