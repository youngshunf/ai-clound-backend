"""网关 Service

支持 OpenAI 和 Anthropic 两种 API 格式的智能路由：
- 如果请求格式与目标供应商格式一致，直接转发
- 如果格式不一致，自动进行格式转换
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.gateway import llm_gateway
from backend.app.llm.schema.proxy import (
    AnthropicMessageRequest,
    AnthropicMessageResponse,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from backend.app.llm.service.api_key_service import api_key_service
from backend.common.log import log
from backend.core.conf import settings
from backend.database.db import async_db_session


class GatewayService:
    """网关服务 - 智能格式路由"""

    async def chat_completion(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: ChatCompletionRequest,
        ip_address: str | None = None,
    ) -> ChatCompletionResponse:
        """
        OpenAI 格式聊天补全（非流式）
        """
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        return await llm_gateway.chat_completion(
            db,
            request=request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            daily_limit=rate_limits['daily_token_limit'],
            monthly_limit=rate_limits['monthly_token_limit'],
            ip_address=ip_address,
        )

    async def chat_completion_stream(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: ChatCompletionRequest,
        ip_address: str | None = None,
    ) -> AsyncIterator[str]:
        """
        OpenAI 格式聊天补全（流式）
        
        注意：流式响应需要在内部管理数据库会话，
        因为 FastAPI 会在返回 StreamingResponse 后关闭外部 db 会话
        """
        # 先使用外部 db 验证 API Key
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)
        
        # 保存需要的信息
        user_id = api_key_record.user_id
        api_key_id = api_key_record.id
        rpm_limit = rate_limits['rpm_limit']
        daily_limit = rate_limits['daily_token_limit']
        monthly_limit = rate_limits['monthly_token_limit']

        # 在流式响应中创建新的数据库会话
        async with async_db_session() as stream_db:
            async for chunk in llm_gateway.chat_completion_stream(
                stream_db,
                request=request,
                user_id=user_id,
                api_key_id=api_key_id,
                rpm_limit=rpm_limit,
                daily_limit=daily_limit,
                monthly_limit=monthly_limit,
                ip_address=ip_address,
            ):
                yield chunk

    async def anthropic_messages(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: AnthropicMessageRequest,
        ip_address: str | None = None,
    ) -> AnthropicMessageResponse:
        """
        Anthropic Messages API（非流式）
        
        智能路由：
        - 目标是 Anthropic 供应商：直接转发原始请求
        - 目标是 OpenAI 供应商：转换格式后调用
        """
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        # 调用网关，传递原始 Anthropic 请求
        response = await llm_gateway.chat_completion_anthropic(
            db,
            request=request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            daily_limit=rate_limits['daily_token_limit'],
            monthly_limit=rate_limits['monthly_token_limit'],
            ip_address=ip_address,
        )
        
        # 调试日志：记录返回给前端的 Anthropic 格式响应
        if getattr(settings, 'LITELLM_DEBUG', False):
            content_preview = ''
            if response.content:
                first_block = response.content[0]
                if hasattr(first_block, 'text') and first_block.text:
                    text = first_block.text
                    content_preview = text[:100] + '...' if len(text) > 100 else text
            log.info(
                f'[DEBUG] Anthropic 输出 | model: {response.model} | '
                f'stop_reason: {response.stop_reason} | '
                f'tokens: in:{response.usage.input_tokens} out:{response.usage.output_tokens} | '
                f'内容: {content_preview}'
            )
        
        return response

    async def prepare_anthropic_stream(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: AnthropicMessageRequest,
        ip_address: str | None = None,
    ) -> dict:
        """
        准备 Anthropic 流式响应所需的所有数据（在数据库会话内完成）
        """
        api_key_record = await api_key_service.verify_api_key(db, api_key)
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)
        
        return await llm_gateway.prepare_anthropic_stream(
            db,
            request=request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            daily_limit=rate_limits['daily_token_limit'],
            monthly_limit=rate_limits['monthly_token_limit'],
            ip_address=ip_address,
        )

    async def execute_anthropic_stream(
        self,
        stream_context: dict,
    ) -> AsyncIterator[str]:
        """
        执行 Anthropic 流式响应（不需要数据库）
        """
        async for chunk in llm_gateway.execute_anthropic_stream(stream_context):
            yield chunk


gateway_service = GatewayService()
