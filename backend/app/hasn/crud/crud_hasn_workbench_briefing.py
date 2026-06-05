from typing import Any, Sequence

from sqlalchemy import Row, Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnWorkbenchBriefing
from backend.app.hasn.schema.hasn_workbench_briefing import CreateHasnWorkbenchBriefingParam, UpdateHasnWorkbenchBriefingParam
from backend.utils.timezone import timezone


class CRUDHasnWorkbenchBriefing(CRUDPlus[HasnWorkbenchBriefing]):
    @staticmethod
    async def get_latest_by_owner(
        db: AsyncSession, owner_hasn_id: str, period: str | None = None
    ) -> HasnWorkbenchBriefing | None:
        """取某 owner 的最新简报：给定 period 则精确取该日，否则取 generated_at 最新一份。"""
        stmt = select(HasnWorkbenchBriefing).where(HasnWorkbenchBriefing.owner_hasn_id == owner_hasn_id)
        if period:
            stmt = stmt.where(HasnWorkbenchBriefing.period == period)
        stmt = stmt.order_by(HasnWorkbenchBriefing.generated_at.desc(), HasnWorkbenchBriefing.id.desc()).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_summaries_by_owner(
        db: AsyncSession, owner_hasn_id: str, limit: int = 60
    ) -> Sequence[Row[Any]]:
        """列某 owner 历史简报摘要（period 倒序）：period/state/产出时间/总览/关注项数/计划项数。

        用 JSONB 函数取计数与总览，避免为列表拉回整份文档（document_json 可能不小）。
        """
        doc = HasnWorkbenchBriefing.document_json
        stmt = (
            select(
                HasnWorkbenchBriefing.period,
                HasnWorkbenchBriefing.state,
                doc['generated_at'].astext.label('generated_at'),
                doc['summary'].astext.label('summary'),
                func.coalesce(func.jsonb_array_length(doc['focus_items']), 0).label('focus_count'),
                func.coalesce(func.jsonb_array_length(doc['plans']), 0).label('plan_count'),
            )
            .where(HasnWorkbenchBriefing.owner_hasn_id == owner_hasn_id)
            .order_by(HasnWorkbenchBriefing.period.desc())
            .limit(limit)
        )
        return (await db.execute(stmt)).all()

    @staticmethod
    async def upsert_by_owner_period(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        period: str,
        state: str,
        document_json: dict,
    ) -> HasnWorkbenchBriefing:
        """按 (owner, period) 覆盖式 upsert（当日最新一份）。冲突即更新主脑/状态/文档/产出时间。"""
        now = timezone.now()
        stmt = (
            pg_insert(HasnWorkbenchBriefing)
            .values(
                owner_hasn_id=owner_hasn_id,
                agent_hasn_id=agent_hasn_id,
                period=period,
                state=state,
                document_json=document_json,
                generated_at=now,
                created_time=now,
                updated_time=now,
            )
            .on_conflict_do_update(
                index_elements=[HasnWorkbenchBriefing.owner_hasn_id, HasnWorkbenchBriefing.period],
                set_={
                    'agent_hasn_id': agent_hasn_id,
                    'state': state,
                    'document_json': document_json,
                    'generated_at': now,
                    'updated_time': now,
                },
            )
            .returning(HasnWorkbenchBriefing.id)
        )
        result = await db.execute(stmt)
        await db.flush()
        briefing_id = result.scalar_one()
        return (
            await db.execute(select(HasnWorkbenchBriefing).where(HasnWorkbenchBriefing.id == briefing_id))
        ).scalar_one()

    async def get(self, db: AsyncSession, pk: int) -> HasnWorkbenchBriefing | None:
        """
        获取HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台每日关注简报（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 工作台每日关注简报（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnWorkbenchBriefing]:
        """
        获取所有HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnWorkbenchBriefingParam) -> None:
        """
        创建HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param obj: 创建HASN 工作台每日关注简报（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnWorkbenchBriefingParam) -> int:
        """
        更新HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param pk: HASN 工作台每日关注简报（云端权威） ID
        :param obj: 更新 HASN 工作台每日关注简报（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 工作台每日关注简报（云端权威）

        :param db: 数据库会话
        :param pks: HASN 工作台每日关注简报（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_workbench_briefing_dao: CRUDHasnWorkbenchBriefing = CRUDHasnWorkbenchBriefing(HasnWorkbenchBriefing)
