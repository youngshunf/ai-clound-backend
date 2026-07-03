from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgents
from backend.app.hasn.schema.hasn_agents import CreateHasnAgentsParam, UpdateHasnAgentsParam


class CRUDHasnAgents(CRUDPlus[HasnAgents]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAgents | None:
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAgents]:
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAgentsParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAgentsParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    # ─── 业务查询方法 ───

    @staticmethod
    async def get_by_hasn_id(db: AsyncSession, hasn_id: str) -> HasnAgents | None:
        return (await db.execute(
            select(HasnAgents).where(HasnAgents.hasn_id == hasn_id)
        )).scalars().first()

    @staticmethod
    async def get_by_star_id(db: AsyncSession, star_id: str) -> HasnAgents | None:
        return (await db.execute(
            select(HasnAgents).where(HasnAgents.star_id == star_id)
        )).scalars().first()

    @staticmethod
    async def get_active_agents_by_owner(db: AsyncSession, owner_hasn_id: str) -> Sequence[HasnAgents]:
        """
        查询某个 Owner 的所有活跃 Agent

        :param db: 数据库会话
        :param owner_hasn_id: Owner 的 hasn_id
        :return: Agent 列表
        """
        return (await db.execute(
            select(HasnAgents)
            .where(HasnAgents.owner_id == owner_hasn_id)
            .where(HasnAgents.status == 'active')
            .order_by(HasnAgents.id.asc())
        )).scalars().all()


hasn_agents_dao: CRUDHasnAgents = CRUDHasnAgents(HasnAgents)
