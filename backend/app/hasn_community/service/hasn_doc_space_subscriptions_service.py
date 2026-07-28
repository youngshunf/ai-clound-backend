from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_community.crud.crud_hasn_doc_space_subscriptions import (
    hasn_doc_space_subscriptions_dao,
)
from backend.app.hasn_community.model import HasnDocSpaces, HasnDocSpaceSubscriptions
from backend.app.hasn_community.schema.hasn_doc_space_subscriptions import (
    CreateHasnDocSpaceSubscriptionsParam,
    DeleteHasnDocSpaceSubscriptionsParam,
    UpdateHasnDocSpaceSubscriptionsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.database.db import uuid4_str


class HasnDocSpaceSubscriptionsService:
    """codegen 生成后补充领域约束的文集订阅服务。"""

    @staticmethod
    async def _space(db: AsyncSession, ident: str) -> HasnDocSpaces:
        column = HasnDocSpaces.space_id if ident.startswith('ds_') else HasnDocSpaces.slug
        space = (
            await db.execute(
                select(HasnDocSpaces).where(
                    column == ident,
                    HasnDocSpaces.status == 'active',
                )
            )
        ).scalar_one_or_none()
        if not space:
            raise errors.NotFoundError(msg='文集不存在')
        return space

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

    @classmethod
    async def subscribe(
        cls,
        db: AsyncSession,
        *,
        ident: str,
        subscriber_hasn_id: str,
    ) -> dict[str, Any]:
        """幂等订阅公开或密码文集，并只在真实新增时递增权威计数。"""
        space = await cls._space(db, ident)
        if space.owner_hasn_id == subscriber_hasn_id:
            raise errors.RequestError(msg='不能订阅自己的文集')
        if space.default_visibility == 'private':
            raise errors.ForbiddenError(msg='私有文集不可订阅')
        inserted = (
            await db.execute(
                pg_insert(HasnDocSpaceSubscriptions)
                .values(
                    subscription_id=uuid4_str(),
                    space_id=space.space_id,
                    subscriber_hasn_id=subscriber_hasn_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        HasnDocSpaceSubscriptions.space_id,
                        HasnDocSpaceSubscriptions.subscriber_hasn_id,
                    ]
                )
                .returning(HasnDocSpaceSubscriptions.id)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            await db.execute(
                update(HasnDocSpaces)
                .where(HasnDocSpaces.space_id == space.space_id)
                .values(subscribe_count=HasnDocSpaces.subscribe_count + 1)
            )
        count = (
            await db.execute(
                select(HasnDocSpaces.subscribe_count).where(
                    HasnDocSpaces.space_id == space.space_id
                )
            )
        ).scalar_one()
        return {
            'space_id': space.space_id,
            'is_subscribed': True,
            'subscribe_count': int(count),
        }

    @classmethod
    async def unsubscribe(
        cls,
        db: AsyncSession,
        *,
        ident: str,
        subscriber_hasn_id: str,
    ) -> dict[str, Any]:
        """幂等取消订阅，并将冗余计数防御性夹在零以上。"""
        space = await cls._space(db, ident)
        removed = (
            await db.execute(
                delete(HasnDocSpaceSubscriptions)
                .where(
                    HasnDocSpaceSubscriptions.space_id == space.space_id,
                    HasnDocSpaceSubscriptions.subscriber_hasn_id == subscriber_hasn_id,
                )
                .returning(HasnDocSpaceSubscriptions.id)
            )
        ).scalar_one_or_none()
        if removed is not None:
            await db.execute(
                update(HasnDocSpaces)
                .where(HasnDocSpaces.space_id == space.space_id)
                .values(
                    subscribe_count=func.greatest(
                        HasnDocSpaces.subscribe_count - 1,
                        0,
                    )
                )
            )
        count = (
            await db.execute(
                select(HasnDocSpaces.subscribe_count).where(
                    HasnDocSpaces.space_id == space.space_id
                )
            )
        ).scalar_one()
        return {
            'space_id': space.space_id,
            'is_subscribed': False,
            'subscribe_count': int(count),
        }

    @staticmethod
    async def subscribed_space_ids(
        db: AsyncSession,
        *,
        subscriber_hasn_id: str,
        space_ids: list[str],
    ) -> set[str]:
        """批量返回当前 viewer 已订阅的文集 ID。"""
        if not space_ids:
            return set()
        rows = (
            await db.execute(
                select(HasnDocSpaceSubscriptions.space_id).where(
                    HasnDocSpaceSubscriptions.subscriber_hasn_id == subscriber_hasn_id,
                    HasnDocSpaceSubscriptions.space_id.in_(space_ids),
                )
            )
        ).scalars()
        return set(rows)

    @staticmethod
    async def subscriber_ids(
        db: AsyncSession,
        *,
        space_id: str,
    ) -> list[str]:
        """返回文集的真实订阅者，用于更新通知。"""
        return list(
            (
                await db.execute(
                    select(HasnDocSpaceSubscriptions.subscriber_hasn_id).where(
                        HasnDocSpaceSubscriptions.space_id == space_id
                    )
                )
            ).scalars()
        )

    @staticmethod
    async def list_spaces(
        db: AsyncSession,
        *,
        subscriber_hasn_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[HasnDocSpaces], str | None]:
        """按订阅时间与订阅 ID 进行稳定 keyset 分页。"""
        if limit < 1 or limit > 50:
            raise errors.RequestError(msg='limit 必须在 1 到 50 之间')
        stmt = (
            select(HasnDocSpaceSubscriptions, HasnDocSpaces)
            .join(
                HasnDocSpaces,
                HasnDocSpaces.space_id == HasnDocSpaceSubscriptions.space_id,
            )
            .where(
                HasnDocSpaceSubscriptions.subscriber_hasn_id == subscriber_hasn_id,
                HasnDocSpaces.status == 'active',
                or_(
                    HasnDocSpaces.default_visibility != 'private',
                    HasnDocSpaces.owner_hasn_id == subscriber_hasn_id,
                ),
            )
        )
        if cursor:
            try:
                created_raw, subscription_id = cursor.rsplit('|', 1)
                created_time = datetime.fromisoformat(created_raw)
                if not subscription_id:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise errors.RequestError(msg='订阅文集分页游标无效') from exc
            stmt = stmt.where(
                or_(
                    HasnDocSpaceSubscriptions.created_time < created_time,
                    and_(
                        HasnDocSpaceSubscriptions.created_time == created_time,
                        HasnDocSpaceSubscriptions.subscription_id < subscription_id,
                    ),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(
                    HasnDocSpaceSubscriptions.created_time.desc(),
                    HasnDocSpaceSubscriptions.subscription_id.desc(),
                ).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            subscription = rows[-1].HasnDocSpaceSubscriptions
            next_cursor = (
                f'{subscription.created_time.isoformat()}|'
                f'{subscription.subscription_id}'
            )
        return [row.HasnDocSpaces for row in rows], next_cursor


hasn_doc_space_subscriptions_service = HasnDocSpaceSubscriptionsService()
