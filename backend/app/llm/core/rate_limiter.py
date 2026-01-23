"""速率限制器实现"""

from datetime import date

from backend.common.exception.errors import HTTPError
from backend.database.redis import redis_client


class RateLimitExceeded(HTTPError):
    """速率限制超出异常"""

    def __init__(self, message: str = 'Rate limit exceeded') -> None:
        super().__init__(code=429, msg=message)


class RateLimiter:
    """基于 Redis 的速率限制器"""

    def __init__(self, redis_prefix: str = 'fba:llm') -> None:
        self.redis_prefix = redis_prefix

    def _get_rpm_key(self, api_key_id: int) -> str:
        """获取 RPM 限制的 Redis key"""
        return f'{self.redis_prefix}:rpm:{api_key_id}'

    def _get_tpm_key(self, api_key_id: int) -> str:
        """获取 TPM 限制的 Redis key"""
        return f'{self.redis_prefix}:tpm:{api_key_id}'

    def _get_daily_key(self, api_key_id: int) -> str:
        """获取日限制的 Redis key"""
        today = date.today().isoformat()
        return f'{self.redis_prefix}:daily:{api_key_id}:{today}'

    def _get_monthly_key(self, api_key_id: int) -> str:
        """获取月限制的 Redis key"""
        month = date.today().strftime('%Y-%m')
        return f'{self.redis_prefix}:monthly:{api_key_id}:{month}'

    async def check_rpm(self, api_key_id: int, rpm_limit: int) -> bool:
        """
        检查 RPM 限制

        :param api_key_id: API Key ID
        :param rpm_limit: RPM 限制
        :return: True 通过，False 超限
        :raises RateLimitExceeded: 超出限制时抛出
        """
        key = self._get_rpm_key(api_key_id)
        count = await redis_client.incr(key)

        if count == 1:
            await redis_client.expire(key, 60)

        if count > rpm_limit:
            raise RateLimitExceeded(f'RPM limit exceeded: {count}/{rpm_limit}')

        return True

    async def check_daily_tokens(self, api_key_id: int, daily_limit: int) -> bool:
        """
        检查日 Token 限制

        :param api_key_id: API Key ID
        :param daily_limit: 日限制
        :return: True 通过，False 超限
        :raises RateLimitExceeded: 超出限制时抛出
        """
        key = self._get_daily_key(api_key_id)
        tokens = int(await redis_client.get(key) or 0)

        if tokens >= daily_limit:
            raise RateLimitExceeded(f'Daily token limit exceeded: {tokens}/{daily_limit}')

        return True

    async def check_monthly_tokens(self, api_key_id: int, monthly_limit: int) -> bool:
        """
        检查月 Token 限制

        :param api_key_id: API Key ID
        :param monthly_limit: 月限制
        :return: True 通过，False 超限
        :raises RateLimitExceeded: 超出限制时抛出
        """
        key = self._get_monthly_key(api_key_id)
        tokens = int(await redis_client.get(key) or 0)

        if tokens >= monthly_limit:
            raise RateLimitExceeded(f'Monthly token limit exceeded: {tokens}/{monthly_limit}')

        return True

    async def check_all(
        self,
        api_key_id: int,
        *,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> bool:
        """
        检查所有限制

        :param api_key_id: API Key ID
        :param rpm_limit: RPM 限制
        :param daily_limit: 日 Token 限制
        :param monthly_limit: 月 Token 限制
        :return: True 通过
        :raises RateLimitExceeded: 超出任一限制时抛出
        """
        await self.check_rpm(api_key_id, rpm_limit)
        await self.check_daily_tokens(api_key_id, daily_limit)
        await self.check_monthly_tokens(api_key_id, monthly_limit)
        return True

    async def consume_tokens(self, api_key_id: int, tokens: int) -> None:
        """
        消费 tokens

        :param api_key_id: API Key ID
        :param tokens: 消费的 tokens 数量
        """
        # 更新日计数
        daily_key = self._get_daily_key(api_key_id)
        await redis_client.incrby(daily_key, tokens)
        await redis_client.expire(daily_key, 86400 * 2)  # 2 天过期

        # 更新月计数
        monthly_key = self._get_monthly_key(api_key_id)
        await redis_client.incrby(monthly_key, tokens)
        await redis_client.expire(monthly_key, 86400 * 35)  # 35 天过期

    async def get_current_rpm(self, api_key_id: int) -> int:
        """获取当前 RPM"""
        key = self._get_rpm_key(api_key_id)
        return int(await redis_client.get(key) or 0)

    async def get_daily_tokens(self, api_key_id: int) -> int:
        """获取今日已用 tokens"""
        key = self._get_daily_key(api_key_id)
        return int(await redis_client.get(key) or 0)

    async def get_monthly_tokens(self, api_key_id: int) -> int:
        """获取本月已用 tokens"""
        key = self._get_monthly_key(api_key_id)
        return int(await redis_client.get(key) or 0)

    async def get_usage_info(
        self,
        api_key_id: int,
        *,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> dict:
        """
        获取使用情况

        :param api_key_id: API Key ID
        :param rpm_limit: RPM 限制
        :param daily_limit: 日 Token 限制
        :param monthly_limit: 月 Token 限制
        :return: 使用情况字典
        """
        current_rpm = await self.get_current_rpm(api_key_id)
        daily_tokens = await self.get_daily_tokens(api_key_id)
        monthly_tokens = await self.get_monthly_tokens(api_key_id)

        return {
            'rpm_limit': rpm_limit,
            'current_rpm': current_rpm,
            'daily_token_limit': daily_limit,
            'daily_token_used': daily_tokens,
            'daily_token_remaining': max(0, daily_limit - daily_tokens),
            'monthly_token_limit': monthly_limit,
            'monthly_token_used': monthly_tokens,
            'monthly_token_remaining': max(0, monthly_limit - monthly_tokens),
        }


# 创建全局速率限制器实例
rate_limiter = RateLimiter()
