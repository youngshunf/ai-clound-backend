"""LLM 网关实现
@author Ysf
"""

import json
import time

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.circuit_breaker import CircuitBreaker, circuit_breaker_manager
from backend.app.llm.core.encryption import key_encryption
from backend.app.llm.core.rate_limiter import rate_limiter
from backend.app.llm.core.usage_tracker import RequestTimer, usage_tracker
from backend.app.llm.crud.crud_model_alias import model_alias_dao
from backend.app.llm.crud.crud_model_config import model_config_dao
from backend.app.llm.crud.crud_model_group import model_group_dao
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.model.model_config import ModelConfig
from backend.app.llm.model.provider import ModelProvider
from backend.app.llm.schema.proxy import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
)
from backend.app.user_tier.service.credit_service import credit_service, InsufficientCreditsError
from backend.common.exception.errors import HTTPError
from backend.common.log import log


class LLMGatewayError(HTTPError):
    """LLM 网关错误"""

    def __init__(self, message: str, code: int = 500) -> None:
        super().__init__(code=code, msg=message)


class ModelNotFoundError(LLMGatewayError):
    """模型未找到错误"""

    def __init__(self, model_name: str) -> None:
        super().__init__(f'Model not found: {model_name}', code=404)


class ProviderUnavailableError(LLMGatewayError):
    """供应商不可用错误"""

    def __init__(self, provider_name: str) -> None:
        super().__init__(f'Provider unavailable: {provider_name}', code=503)


class LLMGateway:
    """LLM 统一调用网关"""

    def __init__(self) -> None:
        self._litellm = None
        self._debug_mode = None

    @property
    def debug_mode(self) -> bool:
        """是否开启调试模式"""
        if self._debug_mode is None:
            from backend.core.conf import settings
            self._debug_mode = getattr(settings, 'LITELLM_DEBUG', False)
        return self._debug_mode

    @property
    def litellm(self):
        """延迟加载 litellm"""
        if self._litellm is None:
            import logging
            import litellm

            litellm.drop_params = True  # 忽略不支持的参数
            
            # 完全禁用 LiteLLM 内置日志
            litellm.set_verbose = False
            litellm.suppress_debug_info = True
            logging.getLogger('LiteLLM').setLevel(logging.WARNING)
            
            if self.debug_mode:
                log.info('[LLM Gateway] 调试模式已开启')
            
            self._litellm = litellm
        return self._litellm

    def _log_debug_request(self, params: dict[str, Any], provider_name: str, api_base: str | None) -> None:
        """调试模式下记录请求详情"""
        if not self.debug_mode:
            return
        
        # 隐藏敏感信息
        safe_params = params.copy()
        if 'api_key' in safe_params:
            api_key = safe_params['api_key']
            if api_key:
                safe_params['api_key'] = f'{api_key[:8]}...{api_key[-4:]}' if len(api_key) > 12 else '***'
        
        # 截断 messages 内容
        if 'messages' in safe_params:
            messages = safe_params['messages']
            truncated_messages = []
            for msg in messages:
                msg_copy = msg.copy() if isinstance(msg, dict) else msg
                if isinstance(msg_copy, dict) and 'content' in msg_copy:
                    content = msg_copy['content']
                    if isinstance(content, str) and len(content) > 200:
                        msg_copy['content'] = content[:200] + f'... (截断, 共{len(content)}字符)'
                truncated_messages.append(msg_copy)
            safe_params['messages'] = truncated_messages
        
        target_url = api_base or f'https://api.{provider_name}.com (default)'
        
        log.info(
            f'[DEBUG] LLM 请求 | URL: {target_url} | 供应商: {provider_name} | '
            f'模型: {safe_params.get("model")} | 流式: {safe_params.get("stream", False)}'
        )

    def _log_debug_response(self, response: Any, is_streaming: bool = False, elapsed_ms: int | None = None) -> None:
        """调试模式下记录响应详情"""
        if not self.debug_mode:
            return
        
        elapsed_info = f'{elapsed_ms}ms' if elapsed_ms else 'N/A'
        
        if is_streaming:
            log.info(f'[DEBUG] LLM 响应 | 流式 | 耗时: {elapsed_info}')
        else:
            try:
                # 尝试将响应转为可序列化的格式
                if hasattr(response, 'model_dump'):
                    response_data = response.model_dump()
                elif hasattr(response, 'dict'):
                    response_data = response.dict()
                elif isinstance(response, dict):
                    response_data = response
                else:
                    response_data = str(response)
                
                # 截断响应内容
                content_preview = ''
                if isinstance(response_data, dict):
                    choices = response_data.get('choices', [])
                    if choices and isinstance(choices[0], dict):
                        msg = choices[0].get('message', {})
                        content = msg.get('content', '')
                        if content and len(content) > 100:
                            content_preview = content[:100] + '...'
                        else:
                            content_preview = content or ''
                    usage = response_data.get('usage', {})
                    tokens_info = f"in:{usage.get('prompt_tokens', 0)} out:{usage.get('completion_tokens', 0)}"
                else:
                    content_preview = str(response_data)[:100]
                    tokens_info = 'N/A'
                
                log.info(
                    f'[DEBUG] LLM 响应 | 耗时: {elapsed_info} | tokens: {tokens_info} | '
                    f'内容预览: {content_preview}'
                )
            except Exception as e:
                log.info(f'[DEBUG] LLM 响应 | 耗时: {elapsed_info} | 解析失败: {e}')

    def _log_debug_error(self, error: Exception, provider_name: str, model_name: str) -> None:
        """调试模式下记录错误详情"""
        if not self.debug_mode:
            return
        
        error_msg = str(error)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + '...'
        
        log.error(
            f'[DEBUG] LLM 错误 | 供应商: {provider_name} | 模型: {model_name} | '
            f'类型: {type(error).__name__} | {error_msg}'
        )

    async def _get_model_config(self, db: AsyncSession, model_name: str) -> ModelConfig:
        """获取模型配置"""
        model = await model_config_dao.get_by_name(db, model_name)
        if not model or not model.enabled:
            raise ModelNotFoundError(model_name)
        return model

    async def _resolve_model_alias(
        self, db: AsyncSession, model_name: str
    ) -> tuple[list[tuple[ModelConfig, ModelProvider]], str | None]:
        """
        解析模型别名，返回映射的模型列表

        Args:
            db: 数据库会话
            model_name: 请求的模型名称（可能是别名）

        Returns:
            tuple: (模型配置和供应商列表, 原始别名或 None)
            - 如果是别名：返回映射的模型列表和原始别名
            - 如果不是别名：返回空列表和 None
        """
        # 检查是否是别名
        mapped_models = await model_alias_dao.get_mapped_models(db, model_name)
        if not mapped_models:
            return [], None

        log.info(f'[LLM Gateway] 检测到模型别名: {model_name} -> {[m.model_name for m in mapped_models]}')

        # 获取每个模型的供应商信息，过滤掉不可用的
        result = []
        for model in mapped_models:
            provider = await provider_dao.get(db, model.provider_id)
            if provider and provider.enabled:
                breaker = self._get_circuit_breaker(provider.name)
                if breaker.allow_request():
                    result.append((model, provider))

        return result, model_name

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
            try:
                api_key = key_encryption.decrypt(provider.api_key_encrypted)
            except Exception as e:
                log.error(f'[LLM Gateway] 解密供应商 {provider.name} 的 API Key 失败: {e}')
                raise ProviderUnavailableError(
                    f'供应商 {provider.name} 的 API Key 解密失败，请重新配置'
                )

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

    async def _call_with_failover(
        self,
        db: AsyncSession,
        *,
        models_with_providers: list[tuple[ModelConfig, ModelProvider]],
        request: ChatCompletionRequest,
        user_id: int,
        api_key_id: int,
        ip_address: str | None = None,
        is_streaming: bool = False,
        original_alias: str | None = None,
    ):
        """
        带故障转移的调用（按优先级尝试多个模型）

        Args:
            db: 数据库会话
            models_with_providers: 模型和供应商列表（按优先级排序）
            request: 请求参数
            user_id: 用户 ID
            api_key_id: API Key ID
            ip_address: IP 地址
            is_streaming: 是否流式
            original_alias: 原始别名（用于日志）

        Returns:
            成功时返回 (response, model_config, provider, credit_rate)
        """
        last_error = None

        for model_config, provider in models_with_providers:
            breaker = self._get_circuit_breaker(provider.name)

            # 获取积分费率
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

            # 构建请求参数
            params = self._build_litellm_params(model_config, provider, request)
            params['stream'] = is_streaming
            request_id = usage_tracker.generate_request_id()

            log.info(
                f'[LLM Gateway] 尝试调用模型: {model_config.model_name} '
                f'(供应商: {provider.name})'
                + (f' [别名: {original_alias}]' if original_alias else '')
            )

            # 调试日志：记录请求详情
            self._log_debug_request(params, provider.name, provider.api_base_url)

            timer = RequestTimer().start()
            try:
                response = await self.litellm.acompletion(**params)
                timer.stop()
                breaker.record_success()

                # 调试日志：记录响应详情
                self._log_debug_response(response, is_streaming=is_streaming, elapsed_ms=timer.elapsed_ms)

                log.info(
                    f'[LLM Gateway] 模型调用成功: {model_config.model_name} '
                    f'(耗时: {timer.elapsed_ms}ms)'
                )

                return response, model_config, provider, credit_rate, request_id, timer

            except Exception as e:
                timer.stop()
                breaker.record_failure()
                last_error = e

                # 调试日志：记录错误详情
                self._log_debug_error(e, provider.name, model_config.model_name)

                log.warning(
                    f'[LLM Gateway] 模型调用失败: {model_config.model_name} '
                    f'(供应商: {provider.name}, 错误: {str(e)})，尝试下一个...'
                )

                # 记录失败
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
                    is_streaming=is_streaming,
                    ip_address=ip_address,
                )

                continue

        # 所有模型都失败了
        raise LLMGatewayError(f'All models failed. Last error: {last_error}')

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

        # 检查用户积分 (LLM 调用前检查)
        await credit_service.check_credits(db, user_id)

        # OpenAI 格式请求直接使用模型名称，不做别名映射
        model_config = await self._get_model_config(db, request.model)
        provider = await self._get_provider(db, model_config.provider_id)

        # 获取模型积分费率
        credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

        # 检查熔断器
        breaker = self._get_circuit_breaker(provider.name)
        if not breaker.allow_request():
            # 尝试故障转移
            fallback_models = await self._get_fallback_models(db, model_config.model_type, model_config.id)
            if not fallback_models:
                raise ProviderUnavailableError(provider.name)
            model_config, provider = fallback_models[0]
            breaker = self._get_circuit_breaker(provider.name)
            # 更新积分费率
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

        # 构建请求参数
        params = self._build_litellm_params(model_config, provider, request)
        request_id = usage_tracker.generate_request_id()

        # 调试日志：记录请求详情
        self._log_debug_request(params, provider.name, provider.api_base_url)

        # 调用 LiteLLM
        timer = RequestTimer().start()
        try:
            response = await self.litellm.acompletion(**params)
            timer.stop()
            breaker.record_success()

            # 调试日志：记录响应详情
            self._log_debug_response(response, is_streaming=False, elapsed_ms=timer.elapsed_ms)

        except Exception as e:
            timer.stop()
            breaker.record_failure()

            # 调试日志：记录错误详情
            self._log_debug_error(e, provider.name, model_config.model_name)

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

        # 提取用量信息
        usage = response.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # 计算并扣除积分
        credits_used = credit_service.calculate_credits(input_tokens, output_tokens, credit_rate)
        if credits_used > 0:
            await credit_service.deduct_credits(
                db,
                user_id=user_id,
                credits=credits_used,
                reference_id=request_id,
                reference_type='llm_usage',
                description=f'模型调用: {model_config.model_name}',
                extra_data={
                    'model_name': model_config.model_name,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                },
            )

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

        # 消费 tokens (速率限制)
        await rate_limiter.consume_tokens(api_key_id, input_tokens + output_tokens)

        # 构建响应
        choices = []
        for i, choice in enumerate(response.get('choices', [])):
            message = choice.get('message', {})
            
            # 处理 tool_calls，确保它们是可序列化的 dict
            tool_calls_raw = message.get('tool_calls')
            tool_calls = None
            if tool_calls_raw:
                tool_calls = []
                for tc in tool_calls_raw:
                    if isinstance(tc, dict):
                        tool_calls.append(tc)
                    else:
                        # 将 LiteLLM 对象转换为 dict
                        tc_dict = {
                            'id': getattr(tc, 'id', None),
                            'type': getattr(tc, 'type', 'function'),
                            'function': {
                                'name': getattr(tc.function, 'name', None) if hasattr(tc, 'function') else None,
                                'arguments': getattr(tc.function, 'arguments', None) if hasattr(tc, 'function') else None,
                            } if hasattr(tc, 'function') else None
                        }
                        tool_calls.append(tc_dict)
            
            choices.append(
                ChatCompletionChoice(
                    index=i,
                    message=ChatMessage(
                        role=message.get('role', 'assistant'),
                        content=message.get('content'),
                        tool_calls=tool_calls,
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

        # 检查用户积分 (LLM 调用前检查)
        await credit_service.check_credits(db, user_id)

        # OpenAI 格式请求直接使用模型名称，不做别名映射
        model_config = await self._get_model_config(db, request.model)
        provider = await self._get_provider(db, model_config.provider_id)

        # 获取模型积分费率
        credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

        # 检查熔断器
        breaker = self._get_circuit_breaker(provider.name)
        if not breaker.allow_request():
            fallback_models = await self._get_fallback_models(db, model_config.model_type, model_config.id)
            if not fallback_models:
                raise ProviderUnavailableError(provider.name)
            model_config, provider = fallback_models[0]
            breaker = self._get_circuit_breaker(provider.name)
            # 更新积分费率
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

        # 构建请求参数
        params = self._build_litellm_params(model_config, provider, request)
        params['stream'] = True
        request_id = usage_tracker.generate_request_id()

        # 调试日志：记录请求详情
        self._log_debug_request(params, provider.name, provider.api_base_url)

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

            # 调试日志：记录流式响应完成
            self._log_debug_response(None, is_streaming=True, elapsed_ms=timer.elapsed_ms)

            # 估算 tokens（流式响应可能没有精确的 token 计数）
            input_tokens = len(str(request.messages)) // 4  # 粗略估算
            output_tokens = len(content_buffer) // 4

            # 计算并扣除积分
            credits_used = credit_service.calculate_credits(input_tokens, output_tokens, credit_rate)
            if credits_used > 0:
                await credit_service.deduct_credits(
                    db,
                    user_id=user_id,
                    credits=credits_used,
                    reference_id=request_id,
                    reference_type='llm_usage',
                    description=f'模型调用(流式): {model_config.model_name}',
                    extra_data={
                        'model_name': model_config.model_name,
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens,
                        'streaming': True,
                    },
                )

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

            # 调试日志：记录错误详情
            self._log_debug_error(e, provider.name, model_config.model_name)

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


    # ==================== Anthropic 格式接口 ====================

    def _is_anthropic_provider(self, provider_type: str) -> bool:
        """判断是否是 Anthropic 类型的供应商"""
        return provider_type in {'anthropic', 'bedrock', 'vertex_ai'}

    def _build_anthropic_params(
        self,
        model_config: ModelConfig,
        provider: ModelProvider,
        request: 'AnthropicMessageRequest',
    ) -> dict[str, Any]:
        """
        构建 LiteLLM Anthropic 调用参数
        
        用于 litellm.anthropic.messages.acreate() 调用
        """
        # 解密 API Key
        api_key = None
        if provider.api_key_encrypted:
            try:
                api_key = key_encryption.decrypt(provider.api_key_encrypted)
            except Exception as e:
                log.error(f'[LLM Gateway] 解密供应商 {provider.name} 的 API Key 失败: {e}')
                raise ProviderUnavailableError(
                    f'供应商 {provider.name} 的 API Key 解密失败，请重新配置'
                )

        # 构建模型名称
        has_custom_api_base = bool(provider.api_base_url)
        model_name = self._build_model_name(
            model_config.model_name,
            provider.provider_type,
            force_prefix=has_custom_api_base
        )

        # 构建消息列表 - 保持 Anthropic 原始格式
        messages = []
        for msg in request.messages:
            msg_dict = {'role': msg.role}
            if isinstance(msg.content, str):
                msg_dict['content'] = msg.content
            elif isinstance(msg.content, list):
                # 保持原始的 content blocks 格式
                content_blocks = []
                for block in msg.content:
                    if isinstance(block, dict):
                        content_blocks.append(block)
                    elif hasattr(block, 'model_dump'):
                        content_blocks.append(block.model_dump(exclude_none=True))
                    else:
                        content_blocks.append({'type': 'text', 'text': str(block)})
                msg_dict['content'] = content_blocks
            else:
                msg_dict['content'] = str(msg.content)
            messages.append(msg_dict)

        params = {
            'model': model_name,
            'messages': messages,
            'max_tokens': request.max_tokens,
            'api_key': api_key,
            'stream': request.stream,
        }

        # 设置 API base URL
        if provider.api_base_url:
            params['api_base'] = provider.api_base_url

        # 系统提示
        if request.system:
            params['system'] = request.system

        # 可选参数
        if request.temperature is not None:
            params['temperature'] = request.temperature
        if request.top_p is not None:
            params['top_p'] = request.top_p
        if request.top_k is not None:
            params['top_k'] = request.top_k
        if request.stop_sequences:
            params['stop_sequences'] = request.stop_sequences
        if request.tools:
            params['tools'] = request.tools
        if request.tool_choice:
            params['tool_choice'] = request.tool_choice
        if request.metadata:
            params['metadata'] = request.metadata

        # 详细日志
        log.info(
            f'[LLM Gateway] Anthropic 调用参数: model={model_name}, '
            f'provider_name={provider.name}, provider_type={provider.provider_type}, '
            f'api_base={provider.api_base_url}, has_api_key={bool(api_key)}, stream={request.stream}'
        )

        return params

    async def chat_completion_anthropic(
        self,
        db: AsyncSession,
        *,
        request: 'AnthropicMessageRequest',
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
    ) -> 'AnthropicMessageResponse':
        """
        Anthropic 格式聊天补全（非流式）
        
        使用 LiteLLM 的 anthropic.messages.acreate() 接口
        LiteLLM 会自动处理格式转换，无论目标是 Anthropic 还是 OpenAI 供应商
        """
        from backend.app.llm.schema.proxy import (
            AnthropicContentBlock,
            AnthropicMessageResponse,
            AnthropicUsage,
        )

        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

        # 检查用户积分
        await credit_service.check_credits(db, user_id)

        # 尝试解析模型别名
        alias_models, original_alias = await self._resolve_model_alias(db, request.model)

        if alias_models:
            model_config, provider = alias_models[0]
            log.info(
                f'[LLM Gateway] Anthropic 请求使用别名映射: {original_alias} -> {model_config.model_name} '
                f'(供应商: {provider.name}, 类型: {provider.provider_type})'
            )
        else:
            model_config = await self._get_model_config(db, request.model)
            provider = await self._get_provider(db, model_config.provider_id)

        # 获取模型积分费率
        credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

        # 检查熔断器
        breaker = self._get_circuit_breaker(provider.name)
        if not breaker.allow_request():
            raise ProviderUnavailableError(provider.name)

        # 构建参数
        params = self._build_anthropic_params(model_config, provider, request)
        params['stream'] = False
        request_id = usage_tracker.generate_request_id()
        response_model_name = original_alias or model_config.model_name

        # 调试日志
        self._log_debug_request(params, provider.name, provider.api_base_url)

        # 调用 LiteLLM Anthropic 接口
        timer = RequestTimer().start()
        try:
            response = await self.litellm.anthropic.messages.acreate(**params)
            timer.stop()
            breaker.record_success()

            # 调试日志
            self._log_debug_response(response, is_streaming=False, elapsed_ms=timer.elapsed_ms)

        except Exception as e:
            timer.stop()
            breaker.record_failure()
            self._log_debug_error(e, provider.name, model_config.model_name)

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

        # 提取用量信息 - LiteLLM 返回的是 Anthropic 格式
        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'input_tokens', 0) if usage else 0
        output_tokens = getattr(usage, 'output_tokens', 0) if usage else 0

        # 计算并扣除积分
        credits_used = credit_service.calculate_credits(input_tokens, output_tokens, credit_rate)
        if credits_used > 0:
            await credit_service.deduct_credits(
                db,
                user_id=user_id,
                credits=credits_used,
                reference_id=request_id,
                reference_type='llm_usage',
                description=f'模型调用: {model_config.model_name}',
                extra_data={
                    'model_name': model_config.model_name,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                },
            )

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

        # 消费 tokens (速率限制)
        await rate_limiter.consume_tokens(api_key_id, input_tokens + output_tokens)

        # 构建响应 - LiteLLM 返回的已经是 Anthropic 格式，直接转换为我们的 schema
        content = []
        response_content = getattr(response, 'content', []) or []
        for block in response_content:
            if isinstance(block, dict):
                block_type = block.get('type', 'text')
                if block_type == 'text':
                    content.append(AnthropicContentBlock(type='text', text=block.get('text', '')))
                elif block_type == 'tool_use':
                    content.append(AnthropicContentBlock(
                        type='tool_use',
                        id=block.get('id'),
                        name=block.get('name'),
                        input=block.get('input'),
                    ))
            else:
                block_type = getattr(block, 'type', 'text')
                if block_type == 'text':
                    content.append(AnthropicContentBlock(type='text', text=getattr(block, 'text', '')))
                elif block_type == 'tool_use':
                    content.append(AnthropicContentBlock(
                        type='tool_use',
                        id=getattr(block, 'id', None),
                        name=getattr(block, 'name', None),
                        input=getattr(block, 'input', None),
                    ))

        return AnthropicMessageResponse(
            id=getattr(response, 'id', request_id),
            model=response_model_name,
            content=content,
            stop_reason=getattr(response, 'stop_reason', None),
            usage=AnthropicUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )

    async def prepare_anthropic_stream(
        self,
        db: AsyncSession,
        *,
        request: 'AnthropicMessageRequest',
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """
        准备 Anthropic 流式响应所需的所有信息（在数据库会话内完成）
        
        返回一个上下文 dict，包含执行流式响应所需的所有信息
        """
        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )
        
        # 检查用户积分
        await credit_service.check_credits(db, user_id)
        
        # 尝试解析模型别名
        alias_models, original_alias = await self._resolve_model_alias(db, request.model)

        if alias_models:
            model_config, provider = alias_models[0]
            log.info(
                f'[LLM Gateway] Anthropic 流式请求使用别名映射: {original_alias} -> {model_config.model_name}'
            )
        else:
            model_config = await self._get_model_config(db, request.model)
            provider = await self._get_provider(db, model_config.provider_id)

        # 获取积分费率
        credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)
        response_model_name = original_alias or model_config.model_name

        log.info(f'[LLM Gateway] Anthropic 流式请求: {model_config.model_name}')
        
        # 构建 LiteLLM Anthropic 参数
        params = self._build_anthropic_params(model_config, provider, request)
        params['stream'] = True
        
        return {
            'params': params,
            'response_model_name': response_model_name,
            'model_config': {
                'id': model_config.id,
                'model_name': model_config.model_name,
            },
            'provider': {
                'id': provider.id,
                'name': provider.name,
                'api_base_url': provider.api_base_url,
            },
            'user_id': user_id,
            'api_key_id': api_key_id,
            'credit_rate': float(credit_rate) if credit_rate else 1.0,
            'ip_address': ip_address,
        }

    async def execute_anthropic_stream(
        self,
        context: dict[str, Any],
    ) -> AsyncIterator[str]:
        """
        执行 Anthropic 流式响应（不需要数据库）
        
        使用 LiteLLM 的 anthropic.messages.acreate(stream=True) 接口
        LiteLLM 返回的流式事件已经是 Anthropic SSE 格式
        """
        import codecs
        import traceback
        
        params = context['params']
        response_model_name = context['response_model_name']
        provider_name = context['provider']['name']
        api_base_url = context['provider']['api_base_url']
        
        if self.debug_mode:
            log.info(f'[DEBUG] Anthropic 流式响应开始 | model: {response_model_name}')
            self._log_debug_request(params, provider_name, api_base_url)
        
        timer = RequestTimer().start()
        
        # 创建增量 UTF-8 解码器，处理多字节字符被分割的情况
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        
        try:
            if self.debug_mode:
                log.info(f'[DEBUG] 开始调用 LiteLLM anthropic.messages.acreate(stream=True)...')
            
            # 使用 LiteLLM Anthropic 流式接口
            response = await self.litellm.anthropic.messages.acreate(**params)
            
            if self.debug_mode:
                log.info(f'[DEBUG] LiteLLM 返回: {type(response)}')
            
            # LiteLLM 返回的已经是 Anthropic SSE 格式的流
            # 返回格式是 bytes，已经包含正确的 SSE 格式 (event: xxx\ndata: {...}\n\n)
            chunk_count = 0
            async for chunk in response:
                if self.debug_mode and chunk_count == 0:
                    chunk_repr = str(chunk)[:300] if chunk else 'None'
                    log.info(f'[DEBUG] 第一个 chunk: {chunk_repr}')
                
                chunk_count += 1
                
                # LiteLLM 返回的是原始 SSE bytes，使用增量解码器透传
                if isinstance(chunk, bytes):
                    decoded = decoder.decode(chunk, final=False)
                    if decoded:
                        yield decoded
                elif isinstance(chunk, str):
                    yield chunk
                else:
                    # 如果是对象格式（某些版本可能返回解析后的对象）
                    if isinstance(chunk, dict):
                        chunk_type = chunk.get('type', '')
                        yield f'event: {chunk_type}\ndata: {json.dumps(chunk)}\n\n'
                    elif hasattr(chunk, 'type'):
                        chunk_type = getattr(chunk, 'type', 'unknown')
                        if hasattr(chunk, 'model_dump'):
                            chunk_dict = chunk.model_dump()
                        elif hasattr(chunk, 'dict'):
                            chunk_dict = chunk.dict()
                        else:
                            chunk_dict = {'type': chunk_type, 'data': str(chunk)}
                        yield f'event: {chunk_type}\ndata: {json.dumps(chunk_dict)}\n\n'
                    else:
                        # 未知格式，尝试直接输出
                        yield f'data: {json.dumps({"type": "unknown", "content": str(chunk)})}\n\n'
            
            # 刷新解码器中剩余的字节
            final_decoded = decoder.decode(b'', final=True)
            if final_decoded:
                yield final_decoded
            
            timer.stop()
            
            if self.debug_mode:
                log.info(f'[DEBUG] Anthropic 流式响应结束 | 共 {chunk_count} 个 chunk | 耗时: {timer.elapsed_ms}ms')
            
        except Exception as e:
            timer.stop()
            log.error(f'[DEBUG] Anthropic 流式响应异常: {e}\n{traceback.format_exc()}')
            if self.debug_mode:
                self._log_debug_error(e, provider_name, context['model_config']['model_name'])
            
            # 发送错误事件
            error_event = {
                'type': 'error',
                'error': {
                    'type': 'api_error',
                    'message': str(e),
                }
            }
            yield f'event: error\ndata: {json.dumps(error_event)}\n\n'

    async def chat_completion_anthropic_stream(
        self,
        db: AsyncSession,
        *,
        request: 'AnthropicMessageRequest',
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Anthropic 格式聊天补全（流式）- 已废弃，使用 prepare_anthropic_stream + execute_anthropic_stream
        """
        context = await self.prepare_anthropic_stream(
            db,
            request=request,
            user_id=user_id,
            api_key_id=api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
            ip_address=ip_address,
        )
        async for chunk in self.execute_anthropic_stream(context):
            yield chunk


# 创建全局网关实例
llm_gateway = LLMGateway()
