from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text, func
from sqlalchemy.ext.asyncio import AsyncSession


class AnalyticsService:
    """分析看板服务"""

    async def get_analytics(self, *, db: AsyncSession, days: int = 30) -> dict:
        now = datetime.now()
        start_date = now - timedelta(days=days)

        # ========== 1. 概览卡片 ==========
        overview = await self._get_overview(db, start_date)

        # ========== 2. 趋势数据（按天） ==========
        trends = await self._get_trends(db, days)

        # ========== 3. 图表数据 ==========
        model_distribution = await self._get_model_distribution(db, start_date)
        tier_distribution = await self._get_tier_distribution(db)
        token_ranking = await self._get_token_ranking(db, start_date)

        return {
            'overview': overview,
            'trends': trends,
            'model_distribution': model_distribution,
            'tier_distribution': tier_distribution,
            'token_ranking': token_ranking,
        }

    async def _get_overview(self, db: AsyncSession, start_date: datetime) -> dict:
        """概览指标"""
        # 总用户数 / 今日新增
        total_users = (await db.execute(
            text('SELECT count(*) FROM hasn_billing.user_subscription')
        )).scalar() or 0

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_today = (await db.execute(
            text('SELECT count(*) FROM hasn_billing.user_subscription WHERE created_time >= :d'),
            {'d': today_start}
        )).scalar() or 0

        # 总积分消耗
        total_usage_credits = (await db.execute(
            text("SELECT COALESCE(SUM(ABS(credits)), 0) FROM hasn_billing.credit_transaction WHERE transaction_type = 'usage'")
        )).scalar() or Decimal('0')

        period_usage_credits = (await db.execute(
            text("SELECT COALESCE(SUM(ABS(credits)), 0) FROM hasn_billing.credit_transaction WHERE transaction_type = 'usage' AND created_time >= :d"),
            {'d': start_date}
        )).scalar() or Decimal('0')

        # API 调用次数
        total_api_calls = (await db.execute(
            text('SELECT count(*) FROM llm_usage_log')
        )).scalar() or 0

        period_api_calls = (await db.execute(
            text('SELECT count(*) FROM llm_usage_log WHERE created_time >= :d'),
            {'d': start_date}
        )).scalar() or 0

        # 充值收入（purchase + subscription_upgrade）
        total_income_credits = (await db.execute(
            text("SELECT COALESCE(SUM(credits), 0) FROM hasn_billing.credit_transaction WHERE transaction_type IN ('purchase', 'subscription_upgrade')")
        )).scalar() or Decimal('0')

        period_income_credits = (await db.execute(
            text("SELECT COALESCE(SUM(credits), 0) FROM hasn_billing.credit_transaction WHERE transaction_type IN ('purchase', 'subscription_upgrade') AND created_time >= :d"),
            {'d': start_date}
        )).scalar() or Decimal('0')

        return {
            'total_users': total_users,
            'new_users_today': new_users_today,
            'total_usage_credits': float(total_usage_credits),
            'period_usage_credits': float(period_usage_credits),
            'total_api_calls': total_api_calls,
            'period_api_calls': period_api_calls,
            'total_income_credits': float(total_income_credits),
            'period_income_credits': float(period_income_credits),
        }

    async def _get_trends(self, db: AsyncSession, days: int) -> dict:
        """按天趋势"""
        # API 调用趋势
        api_rows = (await db.execute(
            text("""
                SELECT d::date as day, COALESCE(c, 0) as count
                FROM generate_series(
                    CURRENT_DATE - :days * interval '1 day',
                    CURRENT_DATE,
                    '1 day'
                ) d
                LEFT JOIN (
                    SELECT date_trunc('day', created_time)::date as day, count(*) as c
                    FROM llm_usage_log
                    WHERE created_time >= CURRENT_DATE - :days * interval '1 day'
                    GROUP BY 1
                ) t ON d::date = t.day
                ORDER BY d
            """),
            {'days': days}
        )).fetchall()

        # 积分消耗趋势
        credit_rows = (await db.execute(
            text("""
                SELECT d::date as day, COALESCE(c, 0) as total
                FROM generate_series(
                    CURRENT_DATE - :days * interval '1 day',
                    CURRENT_DATE,
                    '1 day'
                ) d
                LEFT JOIN (
                    SELECT date_trunc('day', created_time)::date as day,
                           SUM(ABS(credits)) as c
                    FROM hasn_billing.credit_transaction
                    WHERE transaction_type = 'usage'
                      AND created_time >= CURRENT_DATE - :days * interval '1 day'
                    GROUP BY 1
                ) t ON d::date = t.day
                ORDER BY d
            """),
            {'days': days}
        )).fetchall()

        # Token 消耗趋势
        token_rows = (await db.execute(
            text("""
                SELECT d::date as day, COALESCE(c, 0) as total
                FROM generate_series(
                    CURRENT_DATE - :days * interval '1 day',
                    CURRENT_DATE,
                    '1 day'
                ) d
                LEFT JOIN (
                    SELECT date_trunc('day', created_time)::date as day,
                           SUM(total_tokens) as c
                    FROM llm_usage_log
                    WHERE created_time >= CURRENT_DATE - :days * interval '1 day'
                    GROUP BY 1
                ) t ON d::date = t.day
                ORDER BY d
            """),
            {'days': days}
        )).fetchall()

        return {
            'dates': [str(r[0]) for r in api_rows],
            'api_calls': [int(r[1]) for r in api_rows],
            'credit_usage': [float(r[1]) for r in credit_rows],
            'token_usage': [int(r[1]) for r in token_rows],
        }

    async def _get_model_distribution(self, db: AsyncSession, start_date: datetime) -> list[dict]:
        """模型调用分布"""
        rows = (await db.execute(
            text("""
                SELECT model_name, count(*) as calls
                FROM llm_usage_log
                WHERE created_time >= :d
                GROUP BY model_name
                ORDER BY calls DESC
                LIMIT 10
            """),
            {'d': start_date}
        )).fetchall()
        return [{'name': r[0], 'value': int(r[1])} for r in rows]

    async def _get_tier_distribution(self, db: AsyncSession) -> list[dict]:
        """订阅等级分布"""
        rows = (await db.execute(
            text("""
                SELECT tier, count(*) as cnt
                FROM hasn_billing.user_subscription
                WHERE status = 'active'
                GROUP BY tier
                ORDER BY cnt DESC
            """)
        )).fetchall()

        tier_names = {
            'free': '免费版', 'starter': '入门版', 'basic': '基础版',
            'pro': '专业版', 'max': '高级版', 'ultra': '旗舰版',
            'flagship': '超新星',
        }
        return [{'name': tier_names.get(r[0], r[0]), 'value': int(r[1])} for r in rows]

    async def _get_token_ranking(self, db: AsyncSession, start_date: datetime) -> list[dict]:
        """Token 消耗排行（按模型）"""
        rows = (await db.execute(
            text("""
                SELECT model_name,
                       SUM(input_tokens) as input_t,
                       SUM(output_tokens) as output_t,
                       SUM(total_tokens) as total_t,
                       count(*) as calls
                FROM llm_usage_log
                WHERE created_time >= :d
                GROUP BY model_name
                ORDER BY total_t DESC
                LIMIT 10
            """),
            {'d': start_date}
        )).fetchall()
        return [{
            'model': r[0],
            'input_tokens': int(r[1]),
            'output_tokens': int(r[2]),
            'total_tokens': int(r[3]),
            'calls': int(r[4]),
        } for r in rows]


analytics_service: AnalyticsService = AnalyticsService()
