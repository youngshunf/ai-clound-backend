from typing import Sequence

from sqlalchemy import BigInteger, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.admin.model import User
from backend.app.billing.model import CreditTransaction
from backend.app.billing.schema.credit_transaction import CreateCreditTransactionParam, UpdateCreditTransactionParam


class CRUDCreditTransaction(CRUDPlus[CreditTransaction]):
    async def get(self, db: AsyncSession, pk: int) -> CreditTransaction | None:
        """
        获取积分交易记录

        :param db: 数据库会话
        :param pk: 积分交易记录 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(
        self,
        *,
        user_keyword: str | None = None,
        transaction_type: str | None = None,
        reference_id: str | None = None,
        reference_type: str | None = None,
    ) -> Select:
        """获取积分交易记录列表查询表达式，支持用户昵称/手机号搜索"""
        stmt = select(
            CreditTransaction.id,
            CreditTransaction.user_id,
            User.nickname.label('user_nickname'),
            User.phone.label('user_phone'),
            CreditTransaction.transaction_type,
            CreditTransaction.credits,
            CreditTransaction.balance_before,
            CreditTransaction.balance_after,
            CreditTransaction.reference_id,
            CreditTransaction.reference_type,
            CreditTransaction.description,
            CreditTransaction.extra_data,
            CreditTransaction.created_time,
            CreditTransaction.updated_time,
        ).outerjoin(User, CreditTransaction.user_id == User.id)

        if user_keyword is not None:
            stmt = stmt.where(
                or_(
                    User.nickname.ilike(f'%{user_keyword}%'),
                    User.phone.ilike(f'%{user_keyword}%'),
                )
            )
        if transaction_type is not None:
            stmt = stmt.where(CreditTransaction.transaction_type == transaction_type)
        if reference_id is not None:
            stmt = stmt.where(CreditTransaction.reference_id.ilike(f'%{reference_id}%'))
        if reference_type is not None:
            stmt = stmt.where(CreditTransaction.reference_type == reference_type)

        stmt = stmt.order_by(CreditTransaction.id.desc())
        return stmt

    async def get_select_by_user(
        self,
        *,
        user_id: int,
        app_code: str,
        transaction_type: str | None = None,
        reference_type: str | None = None,
    ) -> Select:
        """用户端积分流水查询表达式（强制 user_id + app_code 隔离，按 created_time 倒序）。

        不 join User（用户端无需昵称/手机号），仅返回自己的流水。
        select 整行实体（非列元组）——分页层 `paging_data` 会对结果 `.unique()`，
        而 JSONB `extra_data` 列在元组里非 hashable 会炸；ORM 实体可哈希，规避该坑。
        """
        stmt = select(CreditTransaction).where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.app_code == app_code,
        )
        if transaction_type is not None:
            stmt = stmt.where(CreditTransaction.transaction_type == transaction_type)
        if reference_type is not None:
            stmt = stmt.where(CreditTransaction.reference_type == reference_type)
        return stmt.order_by(CreditTransaction.created_time.desc(), CreditTransaction.id.desc())

    async def get_daily_aggregate_by_user(
        self,
        *,
        user_id: int,
        app_code: str,
        tz: str = 'Asia/Shanghai',
    ) -> Select:
        """用户端积分流水「按日聚合」查询表达式（强制 user_id + app_code 隔离）。

        按本地日（默认 Asia/Shanghai）`date_trunc` 分组，倒序；每组返回当日消耗合计
        （≤0）、入账合计（≥0）、净变动、笔数，以及 **LLM 用量统计**：请求次数（usage 类
        交易笔数）与消耗 token 数（从 usage 交易的 `extra_data.input_tokens/output_tokens`
        累加）。token / 请求数与积分取自**同一批 usage 交易行**，保证与当日消耗口径一致。
        聚合在 DB 完成，避免把每次 LLM 请求都下发到客户端再聚合（否则分页会在日边界切断）。
        """
        local_ts = func.timezone(tz, CreditTransaction.created_time)
        day = func.date_trunc('day', local_ts).label('day')
        consumed = func.coalesce(
            func.sum(case((CreditTransaction.credits < 0, CreditTransaction.credits), else_=0)), 0
        ).label('consumed')
        granted = func.coalesce(
            func.sum(case((CreditTransaction.credits > 0, CreditTransaction.credits), else_=0)), 0
        ).label('granted')
        net = func.coalesce(func.sum(CreditTransaction.credits), 0).label('net')
        cnt = func.count().label('cnt')
        # LLM 用量：仅统计 usage 交易。token 从 extra_data JSONB 取出文本再转 bigint，
        # 缺字段 / 非 usage 行均 coalesce 为 0（Postgres CASE 对 else 分支短路，不会误算）。
        is_usage = CreditTransaction.transaction_type == 'usage'
        input_tok = func.coalesce(CreditTransaction.extra_data['input_tokens'].astext.cast(BigInteger), 0)
        output_tok = func.coalesce(CreditTransaction.extra_data['output_tokens'].astext.cast(BigInteger), 0)
        request_count = func.coalesce(func.sum(case((is_usage, 1), else_=0)), 0).label('request_count')
        token_count = func.coalesce(
            func.sum(case((is_usage, input_tok + output_tok), else_=0)), 0
        ).label('token_count')
        return (
            select(day, consumed, granted, net, cnt, request_count, token_count)
            .where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.app_code == app_code,
            )
            .group_by(day)
            .order_by(day.desc())
        )

    async def get_all(self, db: AsyncSession) -> Sequence[CreditTransaction]:
        """
        获取所有积分交易记录

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCreditTransactionParam) -> None:
        """
        创建积分交易记录

        :param db: 数据库会话
        :param obj: 创建积分交易记录参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCreditTransactionParam) -> int:
        """
        更新积分交易记录

        :param db: 数据库会话
        :param pk: 积分交易记录 ID
        :param obj: 更新 积分交易记录参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除积分交易记录

        :param db: 数据库会话
        :param pks: 积分交易记录 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


credit_transaction_dao: CRUDCreditTransaction = CRUDCreditTransaction(CreditTransaction)
