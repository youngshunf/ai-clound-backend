from typing import Any, Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_sync_business_receipts import hasn_sync_business_receipts_dao
from backend.app.hasn.model import HasnSyncBusinessReceipts
from backend.app.hasn.schema.hasn_sync_business_receipts import CreateHasnSyncBusinessReceiptsParam, DeleteHasnSyncBusinessReceiptsParam, UpdateHasnSyncBusinessReceiptsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSyncBusinessReceiptsService:
    @staticmethod
    async def reserve(
        *,
        db: AsyncSession,
        idempotency_key: str,
        owner_id: str,
        node_id: str,
        client_event_id: str,
        event_type: str,
    ) -> bool:
        """在业务事务内预留幂等回执；首次返回 True，已应用返回 False。"""
        inserted = (
            await db.execute(
                sa.text(
                    'INSERT INTO public.hasn_sync_business_receipts ('
                    'idempotency_key, owner_id, node_id, client_event_id, '
                    'event_type, applied_at, created_time, updated_time'
                    ') VALUES ('
                    ':idempotency_key, :owner_id, :node_id, :client_event_id, '
                    ':event_type, now(), now(), now()'
                    ') ON CONFLICT (idempotency_key) DO NOTHING '
                    'RETURNING id'
                ),
                {
                    'idempotency_key': idempotency_key,
                    'owner_id': owner_id,
                    'node_id': node_id,
                    'client_event_id': client_event_id,
                    'event_type': event_type,
                },
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = (
            await db.execute(
                sa.text(
                    'SELECT owner_id, node_id, client_event_id, event_type '
                    'FROM public.hasn_sync_business_receipts '
                    'WHERE idempotency_key = :idempotency_key'
                ),
                {'idempotency_key': idempotency_key},
            )
        ).mappings().one()
        expected = (owner_id, node_id, client_event_id, event_type)
        actual = (
            str(existing['owner_id']),
            str(existing['node_id']),
            str(existing['client_event_id']),
            str(existing['event_type']),
        )
        if actual != expected:
            raise RuntimeError('sync 业务 receipt 幂等键与既有事件不一致')
        return False

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSyncBusinessReceipts:
        """
        获取sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param pk: sync inbox 业务应用的事务内幂等回执 ID
        :return:
        """
        hasn_sync_business_receipts = await hasn_sync_business_receipts_dao.get(db, pk)
        if not hasn_sync_business_receipts:
            raise errors.NotFoundError(msg='sync inbox 业务应用的事务内幂等回执不存在')
        return hasn_sync_business_receipts

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取sync inbox 业务应用的事务内幂等回执列表

        :param db: 数据库会话
        :return:
        """
        hasn_sync_business_receipts_select = await hasn_sync_business_receipts_dao.get_select()
        return await paging_data(db, hasn_sync_business_receipts_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSyncBusinessReceipts]:
        """
        获取所有sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :return:
        """
        hasn_sync_business_receipts_list = await hasn_sync_business_receipts_dao.get_all(db)
        return hasn_sync_business_receipts_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSyncBusinessReceiptsParam) -> None:
        """
        创建sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param obj: 创建sync inbox 业务应用的事务内幂等回执参数
        :return:
        """
        await hasn_sync_business_receipts_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSyncBusinessReceiptsParam) -> int:
        """
        更新sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param pk: sync inbox 业务应用的事务内幂等回执 ID
        :param obj: 更新sync inbox 业务应用的事务内幂等回执参数
        :return:
        """
        count = await hasn_sync_business_receipts_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSyncBusinessReceiptsParam) -> int:
        """
        删除sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param obj: sync inbox 业务应用的事务内幂等回执 ID 列表
        :return:
        """
        count = await hasn_sync_business_receipts_dao.delete(db, obj.pks)
        return count


hasn_sync_business_receipts_service: HasnSyncBusinessReceiptsService = HasnSyncBusinessReceiptsService()
