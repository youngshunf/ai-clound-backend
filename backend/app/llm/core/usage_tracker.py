"""用量追踪器实现"""

import time
import uuid

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_usage_log import usage_log_dao
from backend.app.llm.enums import UsageLogStatus


class UsageTracker:
    """用量追踪器"""

    @staticmethod
    def generate_request_id() -> str:
        """生成请求 ID"""
        return f'req-{uuid.uuid4().hex}'

    @staticmethod
    def calculate_cost(
        input_tokens: int,
        output_tokens: int,
        input_cost_per_1k: Decimal,
        output_cost_per_1k: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        计算成本

        :param input_tokens: 输入 tokens
        :param output_tokens: 输出 tokens
        :param input_cost_per_1k: 输入成本/1K tokens
        :param output_cost_per_1k: 输出成本/1K tokens
        :return: (输入成本, 输出成本, 总成本)
        """
        input_cost = (Decimal(input_tokens) / 1000) * input_cost_per_1k
        output_cost = (Decimal(output_tokens) / 1000) * output_cost_per_1k
        total_cost = input_cost + output_cost
        return input_cost, output_cost, total_cost

    async def track_success(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        api_key_id: int,
        model_id: int,
        provider_id: int,
        request_id: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        input_cost_per_1k: Decimal,
        output_cost_per_1k: Decimal,
        latency_ms: int,
        is_streaming: bool = False,
        ip_address: str | None = None,
    ) -> None:
        """
        记录成功调用

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param api_key_id: API Key ID
        :param model_id: 模型 ID
        :param provider_id: 供应商 ID
        :param request_id: 请求 ID
        :param model_name: 模型名称
        :param input_tokens: 输入 tokens
        :param output_tokens: 输出 tokens
        :param input_cost_per_1k: 输入成本/1K tokens
        :param output_cost_per_1k: 输出成本/1K tokens
        :param latency_ms: 延迟(毫秒)
        :param is_streaming: 是否流式
        :param ip_address: IP 地址
        """
        input_cost, output_cost, total_cost = self.calculate_cost(
            input_tokens, output_tokens, input_cost_per_1k, output_cost_per_1k
        )

        await usage_log_dao.create(
            db,
            {
                'user_id': user_id,
                'api_key_id': api_key_id,
                'model_id': model_id,
                'provider_id': provider_id,
                'request_id': request_id,
                'model_name': model_name,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': input_tokens + output_tokens,
                'input_cost': input_cost,
                'output_cost': output_cost,
                'total_cost': total_cost,
                'latency_ms': latency_ms,
                'status': UsageLogStatus.SUCCESS,
                'is_streaming': is_streaming,
                'ip_address': ip_address,
            },
        )

    async def track_error(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        api_key_id: int,
        model_id: int,
        provider_id: int,
        request_id: str,
        model_name: str,
        error_message: str,
        latency_ms: int,
        is_streaming: bool = False,
        ip_address: str | None = None,
    ) -> None:
        """
        记录失败调用

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param api_key_id: API Key ID
        :param model_id: 模型 ID
        :param provider_id: 供应商 ID
        :param request_id: 请求 ID
        :param model_name: 模型名称
        :param error_message: 错误信息
        :param latency_ms: 延迟(毫秒)
        :param is_streaming: 是否流式
        :param ip_address: IP 地址
        """
        await usage_log_dao.create(
            db,
            {
                'user_id': user_id,
                'api_key_id': api_key_id,
                'model_id': model_id,
                'provider_id': provider_id,
                'request_id': request_id,
                'model_name': model_name,
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0,
                'input_cost': Decimal(0),
                'output_cost': Decimal(0),
                'total_cost': Decimal(0),
                'latency_ms': latency_ms,
                'status': UsageLogStatus.ERROR,
                'error_message': error_message,
                'is_streaming': is_streaming,
                'ip_address': ip_address,
            },
        )


class RequestTimer:
    """请求计时器"""

    def __init__(self) -> None:
        self._start_time: float | None = None
        self._end_time: float | None = None

    def start(self) -> 'RequestTimer':
        """开始计时"""
        self._start_time = time.time()
        return self

    def stop(self) -> 'RequestTimer':
        """停止计时"""
        self._end_time = time.time()
        return self

    @property
    def elapsed_ms(self) -> int:
        """获取耗时(毫秒)"""
        if self._start_time is None:
            return 0
        end = self._end_time or time.time()
        return int((end - self._start_time) * 1000)

    def __enter__(self) -> 'RequestTimer':
        return self.start()

    def __exit__(self, *args: object) -> None:
        self.stop()


# 创建全局用量追踪器实例
usage_tracker = UsageTracker()
