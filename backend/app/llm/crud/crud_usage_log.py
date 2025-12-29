"""用量日志 CRUD"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model.usage_log import UsageLog
from backend.app.llm.schema.usage_log import DailyUsage, ModelUsage, UsageSummary


class CRUDUsageLog(CRUDPlus[UsageLog]):
    """用量日志数据库操作类"""

    async def get(self, db: AsyncSession, pk: int) -> UsageLog | None:
        return await self.select_model(db, pk)

    async def get_by_request_id(self, db: AsyncSession, request_id: str) -> UsageLog | None:
        return await self.select_model_by_column(db, request_id=request_id)

    async def get_list(
        self,
        *,
        user_id: int | None = None,
        api_key_id: int | None = None,
        model_name: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Select:
        filters = {}
        if user_id is not None:
            filters['user_id'] = user_id
        if api_key_id is not None:
            filters['api_key_id'] = api_key_id
        if model_name is not None:
            filters['model_name__like'] = f'%{model_name}%'
        if status is not None:
            filters['status'] = status
        if start_date is not None:
            filters['created_time__ge'] = datetime.combine(start_date, datetime.min.time())
        if end_date is not None:
            filters['created_time__le'] = datetime.combine(end_date, datetime.max.time())
        return await self.select_order('id', 'desc', **filters)

    async def create(self, db: AsyncSession, obj: dict) -> UsageLog:
        new_obj = UsageLog(**obj)
        db.add(new_obj)
        await db.commit()
        await db.refresh(new_obj)
        return new_obj

    async def get_summary(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> UsageSummary:
        """获取用量汇总"""
        stmt = select(
            func.count(UsageLog.id).label('total_requests'),
            func.sum(func.if_(UsageLog.status == 'SUCCESS', 1, 0)).label('success_requests'),
            func.sum(func.if_(UsageLog.status == 'ERROR', 1, 0)).label('error_requests'),
            func.coalesce(func.sum(UsageLog.total_tokens), 0).label('total_tokens'),
            func.coalesce(func.sum(UsageLog.input_tokens), 0).label('total_input_tokens'),
            func.coalesce(func.sum(UsageLog.output_tokens), 0).label('total_output_tokens'),
            func.coalesce(func.sum(UsageLog.total_cost), Decimal('0')).label('total_cost'),
            func.coalesce(func.avg(UsageLog.latency_ms), 0).label('avg_latency_ms'),
        ).where(UsageLog.user_id == user_id)

        if start_date:
            stmt = stmt.where(UsageLog.created_time >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            stmt = stmt.where(UsageLog.created_time <= datetime.combine(end_date, datetime.max.time()))

        result = await db.execute(stmt)
        row = result.one()

        return UsageSummary(
            total_requests=row.total_requests or 0,
            success_requests=int(row.success_requests or 0),
            error_requests=int(row.error_requests or 0),
            total_tokens=int(row.total_tokens or 0),
            total_input_tokens=int(row.total_input_tokens or 0),
            total_output_tokens=int(row.total_output_tokens or 0),
            total_cost=row.total_cost or Decimal('0'),
            avg_latency_ms=int(row.avg_latency_ms or 0),
        )

    async def get_daily_usage(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        days: int = 30,
    ) -> list[DailyUsage]:
        """获取每日用量"""
        start_date = date.today() - timedelta(days=days - 1)
        stmt = select(
            func.date(UsageLog.created_time).label('date'),
            func.count(UsageLog.id).label('requests'),
            func.coalesce(func.sum(UsageLog.total_tokens), 0).label('tokens'),
            func.coalesce(func.sum(UsageLog.total_cost), Decimal('0')).label('cost'),
        ).where(
            UsageLog.user_id == user_id,
            UsageLog.created_time >= datetime.combine(start_date, datetime.min.time()),
        ).group_by(
            func.date(UsageLog.created_time)
        ).order_by(
            func.date(UsageLog.created_time)
        )

        result = await db.execute(stmt)
        rows = result.all()

        return [
            DailyUsage(
                date=str(row.date),
                requests=row.requests,
                tokens=int(row.tokens),
                cost=row.cost,
            )
            for row in rows
        ]

    async def get_model_usage(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ModelUsage]:
        """获取模型用量"""
        stmt = select(
            UsageLog.model_name,
            func.count(UsageLog.id).label('requests'),
            func.coalesce(func.sum(UsageLog.total_tokens), 0).label('tokens'),
            func.coalesce(func.sum(UsageLog.total_cost), Decimal('0')).label('cost'),
        ).where(UsageLog.user_id == user_id)

        if start_date:
            stmt = stmt.where(UsageLog.created_time >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            stmt = stmt.where(UsageLog.created_time <= datetime.combine(end_date, datetime.max.time()))

        stmt = stmt.group_by(UsageLog.model_name).order_by(func.sum(UsageLog.total_tokens).desc())

        result = await db.execute(stmt)
        rows = result.all()

        return [
            ModelUsage(
                model_name=row.model_name,
                requests=row.requests,
                tokens=int(row.tokens),
                cost=row.cost,
            )
            for row in rows
        ]

    async def get_tokens_today(self, db: AsyncSession, *, user_id: int) -> int:
        """获取今日 tokens"""
        today = date.today()
        stmt = select(
            func.coalesce(func.sum(UsageLog.total_tokens), 0)
        ).where(
            UsageLog.user_id == user_id,
            UsageLog.created_time >= datetime.combine(today, datetime.min.time()),
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)

    async def get_tokens_month(self, db: AsyncSession, *, user_id: int) -> int:
        """获取本月 tokens"""
        today = date.today()
        first_day = today.replace(day=1)
        stmt = select(
            func.coalesce(func.sum(UsageLog.total_tokens), 0)
        ).where(
            UsageLog.user_id == user_id,
            UsageLog.created_time >= datetime.combine(first_day, datetime.min.time()),
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)


usage_log_dao: CRUDUsageLog = CRUDUsageLog(UsageLog)
