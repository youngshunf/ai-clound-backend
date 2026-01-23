"""网关 Service"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.gateway import llm_gateway
from backend.app.llm.schema.proxy import (
    AnthropicContentBlock,
    AnthropicMessageRequest,
    AnthropicMessageResponse,
    AnthropicUsage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
from backend.app.llm.service.api_key_service import api_key_service


class GatewayService:
    """网关服务"""

    @staticmethod
    async def chat_completion(
        db: AsyncSession,
        *,
        api_key: str,
        request: ChatCompletionRequest,
        ip_address: str | None = None,
    ) -> ChatCompletionResponse:
        """
        聊天补全（非流式）

        :param db: 数据库会话
        :param api_key: API Key
        :param request: 请求参数
        :param ip_address: IP 地址
        :return: 聊天补全响应
        """
        # 验证 API Key
        api_key_record = await api_key_service.verify_api_key(db, api_key)

        # 获取速率限制
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        # 调用网关
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

    @staticmethod
    async def chat_completion_stream(
        db: AsyncSession,
        *,
        api_key: str,
        request: ChatCompletionRequest,
        ip_address: str | None = None,
    ) -> AsyncIterator[str]:
        """
        聊天补全（流式）

        :param db: 数据库会话
        :param api_key: API Key
        :param request: 请求参数
        :param ip_address: IP 地址
        :return: SSE 流
        """
        # 验证 API Key
        api_key_record = await api_key_service.verify_api_key(db, api_key)

        # 获取速率限制
        rate_limits = await api_key_service.get_rate_limits(db, api_key_record)

        # 调用网关
        async for chunk in llm_gateway.chat_completion_stream(
            db,
            request=request,
            user_id=api_key_record.user_id,
            api_key_id=api_key_record.id,
            rpm_limit=rate_limits['rpm_limit'],
            daily_limit=rate_limits['daily_token_limit'],
            monthly_limit=rate_limits['monthly_token_limit'],
            ip_address=ip_address,
        ):
            yield chunk

    @staticmethod
    def _convert_anthropic_to_openai(request: AnthropicMessageRequest) -> ChatCompletionRequest:
        """将 Anthropic 格式转换为 OpenAI 格式"""
        messages = []

        # 添加系统消息
        if request.system:
            messages.append(ChatMessage(role='system', content=request.system))

        # 转换消息
        for msg in request.messages:
            if isinstance(msg.content, str):
                content = msg.content
            else:
                # 处理内容块
                text_parts = [block.text for block in msg.content if block.type == 'text' and block.text]
                content = '\n'.join(text_parts)

            messages.append(ChatMessage(role=msg.role, content=content))

        return ChatCompletionRequest(
            model=request.model,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stream=request.stream,
            stop=request.stop_sequences,
            tools=request.tools,
            tool_choice=request.tool_choice,
        )

    @staticmethod
    def _convert_openai_to_anthropic(response: ChatCompletionResponse) -> AnthropicMessageResponse:
        """将 OpenAI 响应转换为 Anthropic 格式"""
        content = []
        stop_reason = None

        if response.choices:
            choice = response.choices[0]
            if choice.message.content:
                content.append(AnthropicContentBlock(type='text', text=choice.message.content))
            stop_reason = choice.finish_reason

        usage = AnthropicUsage(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

        return AnthropicMessageResponse(
            id=response.id,
            model=response.model,
            content=content,
            stop_reason=stop_reason,
            usage=usage,
        )

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

        :param db: 数据库会话
        :param api_key: API Key
        :param request: 请求参数
        :param ip_address: IP 地址
        :return: Anthropic 响应
        """
        # 转换为 OpenAI 格式
        openai_request = self._convert_anthropic_to_openai(request)

        # 调用 OpenAI 兼容接口
        openai_response = await self.chat_completion(
            db,
            api_key=api_key,
            request=openai_request,
            ip_address=ip_address,
        )

        # 转换为 Anthropic 格式
        return self._convert_openai_to_anthropic(openai_response)

    async def anthropic_messages_stream(
        self,
        db: AsyncSession,
        *,
        api_key: str,
        request: AnthropicMessageRequest,
        ip_address: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Anthropic Messages API（流式）

        :param db: 数据库会话
        :param api_key: API Key
        :param request: 请求参数
        :param ip_address: IP 地址
        :return: SSE 流
        """
        import json

        # 转换为 OpenAI 格式
        openai_request = self._convert_anthropic_to_openai(request)
        openai_request.stream = True

        # 发送 message_start 事件
        yield f'event: message_start\ndata: {{"type": "message_start", "message": {{"id": "msg_temp", "type": "message", "role": "assistant", "content": [], "model": "{request.model}"}}}}\n\n'

        # 发送 content_block_start 事件
        yield 'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n'

        # 流式转发
        async for chunk in self.chat_completion_stream(
            db,
            api_key=api_key,
            request=openai_request,
            ip_address=ip_address,
        ):
            if chunk.startswith('data: [DONE]'):
                break
            if chunk.startswith('data: '):
                try:
                    data = json.loads(chunk[6:])
                    if data.get('choices'):
                        delta = data['choices'][0].get('delta', {})
                        content = delta.get('content', '')
                        if content:
                            # 转换为 Anthropic 格式
                            anthropic_chunk = {
                                'type': 'content_block_delta',
                                'index': 0,
                                'delta': {'type': 'text_delta', 'text': content},
                            }
                            yield f'event: content_block_delta\ndata: {json.dumps(anthropic_chunk)}\n\n'
                except json.JSONDecodeError:
                    pass

        # 发送 content_block_stop 事件
        yield 'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'

        # 发送 message_delta 事件
        yield 'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 0}}\n\n'

        # 发送 message_stop 事件
        yield 'event: message_stop\ndata: {"type": "message_stop"}\n\n'


gateway_service = GatewayService()
