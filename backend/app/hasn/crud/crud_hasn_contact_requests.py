from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnContactRequests
from backend.app.hasn.schema.hasn_contact_requests import CreateHasnContactRequestsParam, UpdateHasnContactRequestsParam


class CRUDHasnContactRequests(CRUDPlus[HasnContactRequests]):
    @staticmethod
    def _single_contact_request(result: object) -> HasnContactRequests | None:
        """将无关联加载的查询结果收紧为单个好友请求。"""
        if result is not None and not isinstance(result, HasnContactRequests):
            raise TypeError('好友请求单模型查询返回了关联结果')
        return cast(HasnContactRequests | None, result)

    @staticmethod
    def _contact_request_sequence(result: Sequence[object]) -> Sequence[HasnContactRequests]:
        """将无关联加载的查询结果收紧为好友请求序列。"""
        if not all(isinstance(item, HasnContactRequests) for item in result):
            raise TypeError('好友请求列表查询返回了关联结果')
        return cast(Sequence[HasnContactRequests], result)

    async def get(self, db: AsyncSession, pk: int) -> HasnContactRequests | None:
        """
        获取HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）

        :param db: 数据库会话
        :param pk: HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表） ID
        :return:
        """
        return self._single_contact_request(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnContactRequests]:
        """
        获取所有HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）

        :param db: 数据库会话
        :return:
        """
        return self._contact_request_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnContactRequestsParam) -> None:
        """
        创建HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）

        :param db: 数据库会话
        :param obj: 创建HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnContactRequestsParam) -> int:
        """
        更新HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）

        :param db: 数据库会话
        :param pk: HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表） ID
        :param obj: 更新 HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表）

        :param db: 数据库会话
        :param pks: HASN 好友请求表（请求生命周期独立于 hasn_contacts 关系表） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    # ─── 请求生命周期业务方法 ───

    @staticmethod
    async def create_request(
        db: AsyncSession,
        *,
        from_id: str,
        to_id: str,
        to_owner_id: str,
        from_type: str = 'human',
        to_type: str = 'human',
        relation_type: str = 'social',
        requested_trust_level: int = 2,
        message: str | None = None,
        channel_source: str = 'manual',
        add_source: str = 'other',
    ) -> HasnContactRequests:
        """创建一条 pending 好友请求"""
        obj = HasnContactRequests(
            from_id=from_id,
            from_type=from_type,
            to_id=to_id,
            to_type=to_type,
            to_owner_id=to_owner_id,
            relation_type=relation_type,
            requested_trust_level=requested_trust_level,
            message=message,
            status='pending',
            channel_source=channel_source,
            add_source=add_source,
        )
        db.add(obj)
        await db.flush()
        return obj

    @staticmethod
    async def get_active_pending(
        db: AsyncSession, from_id: str, to_id: str, relation_type: str = 'social',
    ) -> HasnContactRequests | None:
        """查同一对 (from,to,relation) 当前唯一的 pending 请求（应用层去重，与部分唯一索引双保险）"""
        return (await db.execute(
            select(HasnContactRequests)
            .where(HasnContactRequests.from_id == from_id)
            .where(HasnContactRequests.to_id == to_id)
            .where(HasnContactRequests.relation_type == relation_type)
            .where(HasnContactRequests.status == 'pending')
        )).scalars().first()

    @staticmethod
    async def get_received_pending(
        db: AsyncSession, to_owner_id: str, limit: int = 20,
    ) -> list[HasnContactRequests]:
        """审批人视角：我收到的待处理请求"""
        return list((await db.execute(
            select(HasnContactRequests)
            .where(HasnContactRequests.to_owner_id == to_owner_id)
            .where(HasnContactRequests.status == 'pending')
            .order_by(HasnContactRequests.created_time.desc())
            .limit(limit)
        )).scalars().all())

    @staticmethod
    async def get_sent_pending(
        db: AsyncSession, from_id: str, limit: int = 20,
    ) -> list[HasnContactRequests]:
        """发起方视角：我发出的待处理请求"""
        return list((await db.execute(
            select(HasnContactRequests)
            .where(HasnContactRequests.from_id == from_id)
            .where(HasnContactRequests.status == 'pending')
            .order_by(HasnContactRequests.created_time.desc())
            .limit(limit)
        )).scalars().all())

    @staticmethod
    async def mark_accepted(
        db: AsyncSession, request_id: int, decided_by: str, resulting_contact_id: int | None = None,
    ) -> None:
        """通过请求（仅 pending → accepted）"""
        await db.execute(
            update(HasnContactRequests)
            .where(HasnContactRequests.id == request_id)
            .where(HasnContactRequests.status == 'pending')
            .values(
                status='accepted',
                decided_by=decided_by,
                decided_at=func.now(),
                resulting_contact_id=resulting_contact_id,
            )
        )

    @staticmethod
    async def mark_rejected(db: AsyncSession, request_id: int, decided_by: str) -> None:
        """拒绝请求（仅 pending → rejected，不建边，重申不被挡）"""
        await db.execute(
            update(HasnContactRequests)
            .where(HasnContactRequests.id == request_id)
            .where(HasnContactRequests.status == 'pending')
            .values(status='rejected', decided_by=decided_by, decided_at=func.now())
        )

    @staticmethod
    async def mark_withdrawn(db: AsyncSession, request_id: int, decided_by: str) -> None:
        """发起方撤回请求（仅 pending → withdrawn）"""
        await db.execute(
            update(HasnContactRequests)
            .where(HasnContactRequests.id == request_id)
            .where(HasnContactRequests.status == 'pending')
            .values(status='withdrawn', decided_by=decided_by, decided_at=func.now())
        )


hasn_contact_requests_dao: CRUDHasnContactRequests = CRUDHasnContactRequests(HasnContactRequests)
