from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.service import quota_to_credits
from backend.common.log import log

_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')


class AnalyticsService:
    """分析看板服务"""

    async def _get_newapi_quota_rows(self, start_date: datetime, end_date: datetime) -> list[dict]:
        """Read LLM usage from new-api's admin data endpoint.

        Empty username means all users in new-api's admin API.
        """
        try:
            return await newapi_admin_client.get_quota_data(
                username='',
                start_timestamp=int(start_date.timestamp()),
                end_timestamp=int(end_date.timestamp()),
            )
        except NewApiError as exc:
            log.warning(f'[analytics] new-api quota data unavailable, fallback to empty: {exc!r}')
            return []

    @staticmethod
    def _summarize_quota_rows(rows: list[dict]) -> dict:
        quota = sum(int(row.get('quota') or 0) for row in rows)
        return {
            'api_calls': sum(int(row.get('count') or 0) for row in rows),
            'token_usage': sum(int(row.get('token_used') or 0) for row in rows),
            'usage_credits': float(quota_to_credits(quota)),
        }

    @staticmethod
    def _date_series(now: datetime, days: int) -> list[date]:
        start = now.date() - timedelta(days=days)
        return [start + timedelta(days=i) for i in range(days + 1)]

    async def get_analytics(self, *, db: AsyncSession, days: int = 30) -> dict:
        now = datetime.now(tz=_SHANGHAI_TZ)
        start_date = now - timedelta(days=days)
        epoch = datetime.fromtimestamp(0, tz=_SHANGHAI_TZ)

        total_quota_rows = await self._get_newapi_quota_rows(epoch, now)
        period_quota_rows = await self._get_newapi_quota_rows(start_date, now)

        # ========== 1. 概览卡片 ==========
        overview = await self._get_overview(
            db,
            start_date,
            total_usage=self._summarize_quota_rows(total_quota_rows),
            period_usage=self._summarize_quota_rows(period_quota_rows),
        )

        # ========== 2. 趋势数据（按天） ==========
        trends = await self._get_trends(db, now, days, period_quota_rows)

        # ========== 3. 图表数据 ==========
        model_distribution = self._get_model_distribution(period_quota_rows)
        tier_distribution = await self._get_tier_distribution(db)
        token_ranking = self._get_token_ranking(period_quota_rows)

        return {
            'overview': overview,
            'trends': trends,
            'model_distribution': model_distribution,
            'tier_distribution': tier_distribution,
            'token_ranking': token_ranking,
        }

    async def _get_overview(
        self,
        db: AsyncSession,
        start_date: datetime,
        *,
        total_usage: dict,
        period_usage: dict,
    ) -> dict:
        """概览指标"""
        # 总用户数 / 今日新增
        total_users = (await db.execute(
            text('SELECT count(*) FROM hasn_billing.user_subscription')
        )).scalar() or 0

        today_start = datetime.now(tz=_SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        new_users_today = (await db.execute(
            text('SELECT count(*) FROM hasn_billing.user_subscription WHERE created_time >= :d'),
            {'d': today_start}
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
            'total_usage_credits': total_usage['usage_credits'],
            'period_usage_credits': period_usage['usage_credits'],
            'total_api_calls': total_usage['api_calls'],
            'period_api_calls': period_usage['api_calls'],
            'total_income_credits': float(total_income_credits),
            'period_income_credits': float(period_income_credits),
        }

    async def _get_trends(
        self,
        db: AsyncSession,
        now: datetime,
        days: int,
        quota_rows: list[dict],
    ) -> dict:
        """按天趋势"""
        days_series = self._date_series(now, days)
        api_by_day = {day: 0 for day in days_series}
        token_by_day = {day: 0 for day in days_series}
        credit_by_day = {day: 0 for day in days_series}

        for row in quota_rows:
            ts = int(row.get('created_at') or 0)
            day = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date()
            if day not in api_by_day:
                continue
            api_by_day[day] += int(row.get('count') or 0)
            token_by_day[day] += int(row.get('token_used') or 0)
            credit_by_day[day] += int(row.get('quota') or 0)

        dates = [day.isoformat() for day in days_series]

        return {
            'dates': dates,
            'api_calls': [api_by_day[day] for day in days_series],
            'credit_usage': [float(quota_to_credits(credit_by_day[day])) for day in days_series],
            'token_usage': [token_by_day[day] for day in days_series],
        }

    def _get_model_distribution(self, rows: list[dict]) -> list[dict]:
        """模型调用分布"""
        by_model: dict[str, int] = {}
        for row in rows:
            model = row.get('model_name') or ''
            by_model[model] = by_model.get(model, 0) + int(row.get('count') or 0)
        sorted_rows = sorted(by_model.items(), key=lambda item: item[1], reverse=True)
        return [{'name': model, 'value': count} for model, count in sorted_rows[:10]]

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

    def _get_token_ranking(self, rows: list[dict]) -> list[dict]:
        """Token 消耗排行（按模型）"""
        by_model: dict[str, dict] = {}
        for row in rows:
            model = row.get('model_name') or ''
            bucket = by_model.setdefault(model, {'model_name': model, 'token_used': 0, 'count': 0})
            bucket['token_used'] += int(row.get('token_used') or 0)
            bucket['count'] += int(row.get('count') or 0)
        sorted_rows = sorted(by_model.values(), key=lambda row: row['token_used'], reverse=True)
        return [{
            'model': row.get('model_name') or '',
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': int(row.get('token_used') or 0),
            'calls': int(row.get('count') or 0),
        } for row in sorted_rows[:10]]


analytics_service: AnalyticsService = AnalyticsService()
