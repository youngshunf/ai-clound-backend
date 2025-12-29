"""LLM 网关实现
@author Ysf
"""

import json
import time
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.circuit_breaker import CircuitBreaker, circuit_breaker_manager
from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.core.rate_limiter import RateLimitExceeded, rate_limiter
from backend.app.llm.core.usage_tracker import RequestTimer, usage_tracker
from backend.app.llm.crud.crud_model_config import model_config_dao
from backend.app.llm.crud.crud_model_group import model_group_dao
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.model.model_config import ModelConfig
from backend.app.llm.model.provider import ModelProvider
from backend.app.llm.schema.proxy import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
)
from backend.common.exception.errors import HTTPError
from backend.common.log import log


class LLMGatewayError(HTTPError):
    """LLM 网关错误"""

    def __init__(self, message: str, code: int = 500):
        super().__init__(code=code, msg=message)


class ModelNotFoundError(LLMGatewayError):
    """模型未找到错误"""

    def __init__(self, model_name: str):
        super().__init__(f'Model not found: {model_name}', code=404)


class ProviderUnavailableError(LLMGatewayError):
    """供应商不可用错误"""

    def __init__(self, provider_name: str):
        super().__init__(f'Provider unavailable: {provider_name}', code=503)


class LLMGateway:
    """LLM 统一调用网关"""

    def __init__(self):
        self._litellm = None

    @property
    def litellm(self):
        """延迟加载 litellm"""
        if self._litellm is None:
            import litellm

            litellm.drop_params = True  # 忽略不支持的参数
            self._litellm = litellm
        return self._litellm

    async def _get_model_config(self, db: AsyncSession, model_name: str) -> ModelConfig:
        """获取模型配置"""
        model = await model_config_dao.get_by_name(db, model_name)
        if not model or not model.enabled:
            raise ModelNotFoundError(model_name)
        return model

    async def _get_provider(self, db: AsyncSession, provider_id: int) -> ModelProvider:
        """获取供应商"""
        provider = await provider_dao.get(db, provider_id)
        if not provider or not provider.enabled:
            raise ProviderUnavailableError(f'Provider ID: {provider_id}')
        return provider

    def _get_circuit_breaker(self, provider_name: str) -> CircuitBreaker:
        """获取熔断器"""
        return circuit_breaker_manager.get_breaker(provider_name)

    async def _get_fallback_models(
        self, db: AsyncSession, model_type: str, exclude_model_id: int
    ) -> list[tuple[ModelConfig, ModelProvider]]:
        """获取故障转移模型列表"""
        group = await model_group_dao.get_by_type(db, model_type)
        if not group or not group.fallback_enabled:
            return []

        fallback_models = []
        for model_id in group.model_ids:
            if model_id == exclude_model_id:
                continue
            model = await model_config_dao.get(db, model_id)
            if model and model.enabled:
                provider = await provider_dao.get(db, model.provider_id)
                if provider and provider.enabled:
                    breaker = self._get_circuit_breaker(provider.name)
                    if breaker.allow_request():
                        fallback_models.append((model, provider))

        return fallback_models

    def _build_litellm_params(
        self,
        model_config: ModelConfig,
        provider: ModelProvider,
        request: ChatCompletionRequest,
    ) -> dict[str, Any]:
        """构建 LiteLLM 调用参数"""
        # 解密 API Key
        api_key = None
        if provider.api_key_encrypted:
            api_key = key_encryption.decrypt(provider.api_key_encrypted)

        # 构建消息列表
        messages = [msg.model_dump(exclude_none=True) for msg in request.messages]

        # 根据 provider_type 构建模型名称
        # 当有自定义 api_base 时，需要显式添加 provider 前缀
        has_custom_api_base = bool(provider.api_base_url)
        model_name = self._build_model_name(
            model_config.model_name,
            provider.provider_type,
            force_prefix=has_custom_api_base
        )

        params = {
            'model': model_name,
            'messages': messages,
            'api_key': api_key,
            'stream': request.stream,
        }

        # 设置 API base URL
        if provider.api_base_url:
            params['api_base'] = provider.api_base_url

        # 详细日志
        log.info(f'[LLM Gateway] 调用参数: model={model_name}, provider_name={provider.name}, '
                 f'provider_type={provider.provider_type}, api_base={provider.api_base_url}, '
                 f'has_api_key={bool(api_key)}, stream={request.stream}')

        # 可选参数
        if request.temperature is not None:
            params['temperature'] = request.temperature
        if request.top_p is not None:
            params['top_p'] = request.top_p
        if request.max_tokens is not None:
            params['max_tokens'] = min(request.max_tokens, model_config.max_tokens)
        if request.stop is not None:
            params['stop'] = request.stop
        if request.presence_penalty is not None:
            params['presence_penalty'] = request.presence_penalty
        if request.frequency_penalty is not None:
            params['frequency_penalty'] = request.frequency_penalty
        if request.tools is not None and model_config.supports_tools:
            params['tools'] = request.tools
        if request.tool_choice is not None:
            params['tool_choice'] = request.tool_choice
        if request.response_format is not None:
            params['response_format'] = request.response_format
        if request.seed is not None:
            params['seed'] = request.seed

        return params

    def _build_model_name(self, model_name: str, provider_type: str, force_prefix: bool = False) -> str:
        """
        根据 provider_type 构建 LiteLLM 模型名称

        LiteLLM 使用模型名称前缀来识别供应商：
        - openai: gpt-4, gpt-3.5-turbo (无前缀)
        - anthropic: claude-3-opus (无前缀，LiteLLM 自动识别)
        - azure: azure/gpt-4
        - bedrock: bedrock/anthropic.claude-3
        - vertex_ai: vertex_ai/claude-3
        - 等等

        Args:
            model_name: 模型名称
            provider_type: 供应商类型
            force_prefix: 强制添加前缀（当有自定义 api_base 时需要）
        """
        # 这些供应商 LiteLLM 可以通过模型名称自动识别，无需前缀
        auto_detect_providers = {'openai', 'anthropic', 'cohere', 'mistral'}

        if provider_type in auto_detect_providers and not force_prefix:
            return model_name

        # 其他供应商或强制前缀时，添加 provider_type 前缀
        return f'{provider_type}/{model_name}'

    async def chat_completion(
        self,
        db: AsyncSession,
        *,
        request: ChatCompletionRequest,
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
    ) -> ChatCompletionResponse:
        """
        聊天补全（非流式）

        :param db: 数据库会话
        :param request: 请求参数
        :param user_id: 用户 ID
        :param api_key_id: API Key ID
        :param rpm_limit: RPM 限制
        :param daily_limit: 日 Token 限制
        :param monthly_limit: 月 Token 限制
        :param ip_address: IP 地址
        :return: 聊天补全响应
        """
        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

        # 获取模型配置
        model_config = await self._get_model_config(db, request.model)
        provider = await self._get_provider(db, model_config.provider_id)

        # 检查熔断器
        breaker = self._get_circuit_breaker(provider.name)
        if not breaker.allow_request():
            # 尝试故障转移
            fallback_models = await self._get_fallback_models(db, model_config.model_type, model_config.id)
            if not fallback_models:
                raise ProviderUnavailableError(provider.name)
            model_config, provider = fallback_models[0]
            breaker = self._get_circuit_breaker(provider.name)

        # 构建请求参数
        params = self._build_litellm_params(model_config, provider, request)
        request_id = usage_tracker.generate_request_id()

        # 调用 LiteLLM
        timer = RequestTimer().start()
        try:
            response = await self.litellm.acompletion(**params)
            timer.stop()
            breaker.record_success()

            # 提取用量信息
            usage = response.get('usage', {})
            input_tokens = usage.get('prompt_tokens', 0)
            output_tokens = usage.get('completion_tokens', 0)

            # 记录用量
            await usage_tracker.track_success(
                db,
                user_id=user_id,
                api_key_id=api_key_id,
                model_id=model_config.id,
                provider_id=provider.id,
                request_id=request_id,
                model_name=model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost_per_1k=model_config.input_cost_per_1k,
                output_cost_per_1k=model_config.output_cost_per_1k,
                latency_ms=timer.elapsed_ms,
                is_streaming=False,
                ip_address=ip_address,
            )

            # 消费 tokens
            await rate_limiter.consume_tokens(api_key_id, input_tokens + output_tokens)

            # 构建响应
            choices = []
            for i, choice in enumerate(response.get('choices', [])):
                message = choice.get('message', {})
                choices.append(
                    ChatCompletionChoice(
                        index=i,
                        message=ChatMessage(
                            role=message.get('role', 'assistant'),
                            content=message.get('content'),
                            tool_calls=message.get('tool_calls'),
                        ),
                        finish_reason=choice.get('finish_reason'),
                    )
                )

            return ChatCompletionResponse(
                id=request_id,
                created=int(time.time()),
                model=model_config.model_name,
                choices=choices,
                usage=ChatCompletionUsage(
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ),
            )

        except Exception as e:
            timer.stop()
            breaker.record_failure()

            # 记录错误
            await usage_tracker.track_error(
                db,
                user_id=user_id,
                api_key_id=api_key_id,
                model_id=model_config.id,
                provider_id=provider.id,
                request_id=request_id,
                model_name=model_config.model_name,
                error_message=str(e),
                latency_ms=timer.elapsed_ms,
                is_streaming=False,
                ip_address=ip_address,
            )

            raise LLMGatewayError(str(e))

    async def chat_completion_stream(
        self,
        db: AsyncSession,
        *,
        request: ChatCompletionRequest,
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
    ) -> AsyncIterator[str]:
        """
        聊天补全（流式）

        :param db: 数据库会话
        :param request: 请求参数
        :param user_id: 用户 ID
        :param api_key_id: API Key ID
        :param rpm_limit: RPM 限制
        :param daily_limit: 日 Token 限制
        :param monthly_limit: 月 Token 限制
        :param ip_address: IP 地址
        :return: SSE 流
        """
        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

        # 获取模型配置
        model_config = await self._get_model_config(db, request.model)
        provider = await self._get_provider(db, model_config.provider_id)

        # 检查熔断器
        breaker = self._get_circuit_breaker(provider.name)
        if not breaker.allow_request():
            fallback_models = await self._get_fallback_models(db, model_config.model_type, model_config.id)
            if not fallback_models:
                raise ProviderUnavailableError(provider.name)
            model_config, provider = fallback_models[0]
            breaker = self._get_circuit_breaker(provider.name)

        # 构建请求参数
        params = self._build_litellm_params(model_config, provider, request)
        params['stream'] = True
        request_id = usage_tracker.generate_request_id()

        timer = RequestTimer().start()
        total_tokens = 0
        content_buffer = ''

        try:
            response = await self.litellm.acompletion(**params)

            async for chunk in response:
                choices = chunk.get('choices', [])
                if not choices:
                    continue

                delta = choices[0].get('delta', {})
                content = delta.get('content', '')
                if content:
                    content_buffer += content
                    total_tokens += 1  # 简单估算

                # 构建 SSE 数据
                chunk_data = ChatCompletionChunk(
                    id=request_id,
                    created=int(time.time()),
                    model=model_config.model_name,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=0,
                            delta=ChatCompletionChunkDelta(
                                role=delta.get('role'),
                                content=content,
                                tool_calls=delta.get('tool_calls'),
                            ),
                            finish_reason=choices[0].get('finish_reason'),
                        )
                    ],
                )

                yield f'data: {chunk_data.model_dump_json()}\n\n'

            # 发送结束标记
            yield 'data: [DONE]\n\n'

            timer.stop()
            breaker.record_success()

            # 估算 tokens（流式响应可能没有精确的 token 计数）
            input_tokens = len(str(request.messages)) // 4  # 粗略估算
            output_tokens = len(content_buffer) // 4

            # 记录用量
            await usage_tracker.track_success(
                db,
                user_id=user_id,
                api_key_id=api_key_id,
                model_id=model_config.id,
                provider_id=provider.id,
                request_id=request_id,
                model_name=model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost_per_1k=model_config.input_cost_per_1k,
                output_cost_per_1k=model_config.output_cost_per_1k,
                latency_ms=timer.elapsed_ms,
                is_streaming=True,
                ip_address=ip_address,
            )

            # 消费 tokens
            await rate_limiter.consume_tokens(api_key_id, input_tokens + output_tokens)

        except Exception as e:
            timer.stop()
            breaker.record_failure()

            # 记录错误
            await usage_tracker.track_error(
                db,
                user_id=user_id,
                api_key_id=api_key_id,
                model_id=model_config.id,
                provider_id=provider.id,
                request_id=request_id,
                model_name=model_config.model_name,
                error_message=str(e),
                latency_ms=timer.elapsed_ms,
                is_streaming=True,
                ip_address=ip_address,
            )

            # 发送错误
            error_data = {'error': {'message': str(e), 'type': 'gateway_error'}}
            yield f'data: {json.dumps(error_data)}\n\n'


# 创建全局网关实例
llm_gateway = LLMGateway()
