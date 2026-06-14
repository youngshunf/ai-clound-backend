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
from backend.app.llm.core.debug_logger import llm_debug_log
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
from backend.app.billing.service.credit_service import credit_service, InsufficientCreditsError
from backend.common.exception.errors import HTTPError
from backend.common.log import log
from backend.database.db import async_db_session


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
            logging.getLogger('LiteLLM Proxy').setLevel(logging.CRITICAL)  # 完全禁止 Proxy 日志
            
            # 禁用 LiteLLM 的成本计算和日志回调，我们使用自己的计费系统
            litellm.success_callback = []
            litellm.failure_callback = []
            litellm._async_success_callback = []
            litellm._async_failure_callback = []
            
            if self.debug_mode:
                log.info('[LLM Gateway] 调试模式已开启')
            
            self._litellm = litellm
        return self._litellm

    def register_model_pricing(self, model_name: str) -> None:
        """
        为自定义模型注册默认价格，避免 LiteLLM passthrough 日志处理器报错
        
        LiteLLM 的 passthrough 日志处理器会尝试计算成本，如果模型不在映射表中会报错。
        我们使用自己的计费系统，这里只是为了避免报错，使用默认价格。
        """
        import litellm
        
        # 检查模型是否已注册
        anthropic_model = f'anthropic/{model_name}'
        if anthropic_model in litellm.model_cost:
            return
        
        # 使用默认价格注册模型（我们不使用这个价格，只是为了避免报错）
        default_pricing = {
            'input_cost_per_token': 0.000003,  # $3 per 1M tokens
            'output_cost_per_token': 0.000015,  # $15 per 1M tokens
            'max_tokens': 8192,
            'max_input_tokens': 200000,
            'max_output_tokens': 8192,
            'litellm_provider': 'anthropic',
        }
        litellm.model_cost[anthropic_model] = default_pricing
        log.debug(f'[LLM Gateway] 注册自定义模型价格: {anthropic_model}')

    def _log_debug_request(self, params: dict[str, Any], provider_name: str, api_base: str | None) -> None:
        """调试模式下记录请求详情（仅写日志文件，不输出到控制台）"""
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
        
        llm_debug_log.info(
            f'[DEBUG] LLM 请求 | URL: {target_url} | 供应商: {provider_name} | '
            f'模型: {safe_params.get("model")} | 流式: {safe_params.get("stream", False)}'
        )

    def _log_debug_response(self, response: Any, is_streaming: bool = False, elapsed_ms: int | None = None) -> None:
        """调试模式下记录响应详情（仅写日志文件，不输出到控制台）"""
        if not self.debug_mode:
            return
        
        elapsed_info = f'{elapsed_ms}ms' if elapsed_ms else 'N/A'
        
        if is_streaming:
            llm_debug_log.info(f'[DEBUG] LLM 响应 | 流式 | 耗时: {elapsed_info}')
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
                
                llm_debug_log.info(
                    f'[DEBUG] LLM 响应 | 耗时: {elapsed_info} | tokens: {tokens_info} | '
                    f'内容预览: {content_preview}'
                )
            except Exception as e:
                llm_debug_log.info(f'[DEBUG] LLM 响应 | 耗时: {elapsed_info} | 解析失败: {e}')

    def _log_debug_error(self, error: Exception, provider_name: str, model_name: str) -> None:
        """调试模式下记录错误详情（仅写日志文件，不输出到控制台）"""
        if not self.debug_mode:
            return
        
        error_msg = self._get_error_message(error)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + '...'
        
        llm_debug_log.error(
            f'[DEBUG] LLM 错误 | 供应商: {provider_name} | 模型: {model_name} | '
            f'类型: {type(error).__name__} | {error_msg}'
        )

    def _get_error_message(self, error: Exception) -> str:
        """
        从异常中提取有用的错误信息
        
        LiteLLM 的 BaseLLMException 的 str() 可能返回空字符串，
        需要尝试从其他属性获取错误信息
        """
        # 尝试获取常见的错误属性
        error_msg = str(error)
        
        # 如果 str(error) 为空，尝试其他属性
        if not error_msg or error_msg.strip() == '':
            # LiteLLM BaseLLMException 可能有 message 属性
            if hasattr(error, 'message') and error.message:
                error_msg = str(error.message)
            # 某些异常有 detail 属性
            elif hasattr(error, 'detail') and error.detail:
                error_msg = str(error.detail)
            # httpx 异常可能有 response 属性
            elif hasattr(error, 'response') and error.response is not None:
                response = error.response
                status_code = getattr(response, 'status_code', 'N/A')
                reason = getattr(response, 'reason_phrase', '')
                error_msg = f'HTTP {status_code}'
                if reason:
                    error_msg += f' {reason}'
                # 尝试获取响应体
                try:
                    if hasattr(response, 'text'):
                        body = response.text[:200] if len(response.text) > 200 else response.text
                        if body:
                            error_msg += f': {body}'
                except Exception:
                    pass
            # LiteLLM 异常可能有 llm_provider 和 status_code
            elif hasattr(error, 'status_code'):
                status_code = getattr(error, 'status_code', 'N/A')
                llm_provider = getattr(error, 'llm_provider', 'unknown')
                error_msg = f'Provider {llm_provider} returned HTTP {status_code}'
            # 回退到异常类型名
            else:
                error_msg = f'{type(error).__name__}'
        
        # 如果还是空，至少返回异常类型
        if not error_msg or error_msg.strip() == '':
            error_msg = f'{type(error).__name__} (no message)'
        
        return error_msg

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

        # 获取每个模型的供应商信息，过滤掉禁用的（熔断检查留给调用方）
        result = []
        for model in mapped_models:
            provider = await provider_dao.get(db, model.provider_id)
            if provider and provider.enabled:
                result.append((model, provider))

        return result, model_name

    async def _resolve_models(
        self, db: AsyncSession, model_name: str
    ) -> tuple[list[tuple[ModelConfig, ModelProvider]], str | None]:
        """
        统一模型解析：精确匹配 → 别名映射 → 同类型降级

        N4 修复：统一熔断检查逻辑
        - 所有阶段都不过滤熔断，统一加入候选列表
        - 最终排序中统一处理（被熔断的排后面）
        - 由 _call_with_failover 在实际调用前检查熔断状态

        Returns:
            (候选模型列表, 原始别名或 None)
        """
        models_with_providers: list[tuple[ModelConfig, ModelProvider]] = []
        original_alias = None
        first_model_type = None
        first_model_id = None

        # 第一步：精确匹配 model_config 表（支持同名多供应商，不过滤熔断，统一交给排序处理）
        matched_models = await model_config_dao.get_all_by_name(db, model_name)
        for model in matched_models:
            provider = await provider_dao.get(db, model.provider_id)
            if provider and provider.enabled:
                models_with_providers.append((model, provider))
                if first_model_type is None:
                    first_model_type = model.model_type
                    first_model_id = model.id

        # 第二步：别名映射（精确匹配未命中时）
        if not models_with_providers:
            alias_models, original_alias = await self._resolve_model_alias(db, model_name)
            if alias_models:
                models_with_providers = alias_models
                if not first_model_type:
                    first_model_type = alias_models[0][0].model_type
                    first_model_id = alias_models[0][0].id
            elif original_alias and not first_model_type:
                # 别名存在但所有映射模型不可用，从原始映射中获取类型信息用于降级
                raw_models = await model_alias_dao.get_mapped_models(db, model_name)
                if raw_models:
                    first_model_type = raw_models[0].model_type
                    first_model_id = raw_models[0].id

        # 第三步：同类型降级（追加 fallback 模型到候选列表末尾）
        if first_model_type:
            fallback_models = await self._get_fallback_models(
                db, first_model_type, first_model_id or 0
            )
            existing_ids = {m.id for m, _ in models_with_providers}
            for m, p in fallback_models:
                if m.id not in existing_ids:
                    models_with_providers.append((m, p))

        # 按熔断状态（未熔断优先）+ priority（大的优先）排序
        if models_with_providers:
            models_with_providers.sort(
                key=lambda mp: (
                    0 if self._get_circuit_breaker(mp[1].name).allow_request() else 1,
                    -mp[0].priority,  # priority 越大越优先
                )
            )

        return models_with_providers, original_alias

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
                    fallback_models.append((model, provider))

        return fallback_models

    def _build_litellm_params(
        self,
        model_config: ModelConfig,
        provider: ModelProvider,
        request: ChatCompletionRequest,
        timeout: int | None = None,
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
            'timeout': timeout or 60,  # P0-2: 默认 60 秒超时
        }

        # 设置 API base URL
        if provider.api_base_url:
            params['api_base'] = provider.api_base_url
            params['base_url'] = provider.api_base_url

        # 当 provider_type=openai 但模型名可能被 litellm 自动识别为其他协议时
        # （如 claude-* 会被识别为 Anthropic），显式指定 custom_llm_provider
        # 强制 litellm 使用 OpenAI 协议，避免协议不匹配导致 tool_calls 丢失
        if has_custom_api_base and provider.provider_type == 'openai':
            params['custom_llm_provider'] = 'openai'

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

        if provider_type in auto_detect_providers:
            # 对于 LiteLLM 可自动识别的供应商，不加前缀
            # 注意：当 provider_type=openai 但模型名像 claude-* 时，
            # litellm 会自动走 Anthropic 协议。这种情况通过
            # _build_litellm_params 中设置 custom_llm_provider='openai' 解决
            return model_name

        # 其他供应商需要前缀让 LiteLLM 识别使用哪种 API 协议
        return f'{provider_type}/{model_name}'

    @staticmethod
    def _is_anthropic_native(provider_type: str) -> bool:
        """
        判断该供应商是否使用 Anthropic 原生 Messages API 协议。

        True  → 用 litellm.anthropic.messages.acreate()（Anthropic 协议）
        False → 用 litellm.acompletion()（OpenAI 协议），并做格式互转

        Bedrock / Vertex AI 虽然底层是 Claude，但 LiteLLM 对它们统一走
        acompletion 并自动完成协议转换，所以这里不列入 anthropic_native。
        """
        return provider_type == 'anthropic'

    def _build_embedding_model_name(
        self, model_name: str, provider_type: str, has_custom_api_base: bool
    ) -> str:
        """
        为 Embedding 构建 LiteLLM 模型名称。

        Embedding 与 Chat 不同：LiteLLM 对未知模型名会报 "Provider NOT provided"，
        因为 embedding 没有和 chat 一样的模型名自动识别机制。

        规则：
        1. provider_type 为 openai 且有自定义 api_base → 加 openai/ 前缀
           （确保 litellm 知道用 OpenAI 协议，同时模型名原样传给上游）
        2. provider_type 不在 auto_detect 列表 → 加 provider_type/ 前缀
        3. 其他情况 → 不加前缀（litellm 能自动识别）
        """
        auto_detect_providers = {'openai', 'anthropic', 'cohere', 'mistral'}

        if provider_type in auto_detect_providers:
            if has_custom_api_base:
                # 自定义 api_base + openai 类型：需要前缀让 litellm 识别协议
                return f'openai/{model_name}'
            return model_name

        return f'{provider_type}/{model_name}'

    def _build_openai_params_from_anthropic(
        self,
        anthropic_params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        将已构建好的 Anthropic 风格参数转换为 OpenAI 兼容参数。

        用于 provider_type != 'anthropic' 但客户端走了 /v1/messages 接口的情况。
        转换规则：
          - system       → messages 首条 role=system 消息
          - stop_sequences → stop
          - top_k        → 删除（OpenAI 不支持）
          - tools        → 保持（LiteLLM 会做工具格式适配）
          - 其余字段原样保留
        """
        params = dict(anthropic_params)

        # 1. 处理 system 提示
        system = params.pop('system', None)
        messages: list[dict] = list(params.get('messages', []))
        if system:
            # 将 system 块（str 或 list[block]）统一展平为字符串
            if isinstance(system, list):
                system_text = '\n'.join(
                    b.get('text', '') if isinstance(b, dict) else getattr(b, 'text', str(b))
                    for b in system
                )
            else:
                system_text = str(system)
            # 插入到消息列表最前面（避免重复插入）
            if not messages or messages[0].get('role') != 'system':
                messages = [{'role': 'system', 'content': system_text}] + messages
        params['messages'] = messages

        # 2. stop_sequences → stop
        stop_sequences = params.pop('stop_sequences', None)
        if stop_sequences and 'stop' not in params:
            params['stop'] = stop_sequences

        # 3. 删除 OpenAI 不支持的参数
        params.pop('top_k', None)

        return params

    @staticmethod
    def _convert_openai_response_to_anthropic(
        response: Any,
        model_name: str,
        request_id: str,
    ) -> Any:
        """
        将 litellm.acompletion() 返回的 OpenAI 格式响应，
        转换为与 litellm.anthropic.messages.acreate() 返回值结构兼容的对象。

        返回一个简单的 SimpleNamespace，后续代码用 getattr 访问字段。
        """
        from types import SimpleNamespace

        # finish_reason 映射
        finish_reason_map = {
            'stop': 'end_turn',
            'length': 'max_tokens',
            'tool_calls': 'tool_use',
            'content_filter': 'stop_sequence',
        }

        choice = None
        raw_content = ''
        finish_reason = 'end_turn'
        tool_calls_raw = None

        choices = getattr(response, 'choices', None) or response.get('choices', [])
        if choices:
            choice = choices[0] if not isinstance(choices[0], dict) else None
            choice_dict = choices[0] if isinstance(choices[0], dict) else None

            if choice is not None:
                message = getattr(choice, 'message', None)
                raw_content = getattr(message, 'content', '') or ''
                finish_reason = finish_reason_map.get(
                    getattr(choice, 'finish_reason', 'stop') or 'stop', 'end_turn'
                )
                tool_calls_raw = getattr(message, 'tool_calls', None)
            elif choice_dict is not None:
                message = choice_dict.get('message', {})
                raw_content = message.get('content', '') or ''
                finish_reason = finish_reason_map.get(
                    choice_dict.get('finish_reason', 'stop') or 'stop', 'end_turn'
                )
                tool_calls_raw = message.get('tool_calls')

        # 构建 content blocks
        content_blocks = []
        if raw_content:
            content_blocks.append(SimpleNamespace(type='text', text=raw_content))
        if tool_calls_raw:
            for tc in tool_calls_raw:
                import json as _json
                if isinstance(tc, dict):
                    fn = tc.get('function', {})
                    args_raw = fn.get('arguments', '{}')
                else:
                    fn = getattr(tc, 'function', None)
                    args_raw = getattr(fn, 'arguments', '{}') if fn else '{}'
                try:
                    args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args = {}
                tc_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                fn_name = fn.get('name') if isinstance(fn, dict) else getattr(fn, 'name', '')
                content_blocks.append(SimpleNamespace(
                    type='tool_use',
                    id=tc_id,
                    name=fn_name,
                    input=args,
                ))

        # 用量
        usage_obj = getattr(response, 'usage', None) or response.get('usage', {})
        if isinstance(usage_obj, dict):
            in_t = usage_obj.get('prompt_tokens', 0)
            out_t = usage_obj.get('completion_tokens', 0)
        else:
            in_t = getattr(usage_obj, 'prompt_tokens', 0)
            out_t = getattr(usage_obj, 'completion_tokens', 0)

        usage = SimpleNamespace(input_tokens=in_t, output_tokens=out_t)

        resp_id = getattr(response, 'id', None) or (
            response.get('id') if isinstance(response, dict) else None
        ) or request_id

        return SimpleNamespace(
            id=resp_id,
            type='message',
            role='assistant',
            model=model_name,
            content=content_blocks,
            stop_reason=finish_reason,
            stop_sequence=None,
            usage=usage,
        )

    async def _convert_openai_stream_to_anthropic_sse(
        self,
        openai_stream,
        model_name: str,
        message_id: str,
    ):
        """
        将 litellm.acompletion(stream=True) 的 OpenAI 格式流，
        转换为 Anthropic SSE 事件流（字符串 yield）。

        Anthropic 流式事件顺序：
          message_start → content_block_start → content_block_delta(s)
          → content_block_stop → message_delta → message_stop
        """
        import json as _json
        import time as _time

        input_tokens = 0
        output_tokens = 0
        full_text = ''
        finish_reason_map = {
            'stop': 'end_turn',
            'length': 'max_tokens',
            'tool_calls': 'tool_use',
        }

        # message_start
        msg_start = {
            'type': 'message_start',
            'message': {
                'id': message_id,
                'type': 'message',
                'role': 'assistant',
                'content': [],
                'model': model_name,
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 0, 'output_tokens': 0},
            },
        }
        yield f'event: message_start\ndata: {_json.dumps(msg_start)}\n\n'

        # content_block_start（index=0，text block）
        yield (
            'event: content_block_start\n'
            'data: {"type":"content_block_start","index":0,'
            '"content_block":{"type":"text","text":""}}\n\n'
        )
        yield 'event: ping\ndata: {"type":"ping"}\n\n'

        finish_reason_raw = 'stop'
        async for chunk in openai_stream:
            # 提取 delta
            choices = getattr(chunk, 'choices', None) or (
                chunk.get('choices', []) if isinstance(chunk, dict) else []
            )
            if not choices:
                # 可能是携带 usage 的最后一个 chunk（stream_options）
                usage_obj = getattr(chunk, 'usage', None) or (
                    chunk.get('usage') if isinstance(chunk, dict) else None
                )
                if usage_obj:
                    if isinstance(usage_obj, dict):
                        input_tokens = usage_obj.get('prompt_tokens', input_tokens)
                        output_tokens = usage_obj.get('completion_tokens', output_tokens)
                    else:
                        input_tokens = getattr(usage_obj, 'prompt_tokens', input_tokens)
                        output_tokens = getattr(usage_obj, 'completion_tokens', output_tokens)
                continue

            c = choices[0]
            if isinstance(c, dict):
                delta = c.get('delta', {})
                text = delta.get('content') or ''
                finish_reason_raw = c.get('finish_reason') or finish_reason_raw
                # stream_options usage
                usage_obj = c.get('usage')
                if not usage_obj:
                    usage_obj = chunk.get('usage') if isinstance(chunk, dict) else None
            else:
                delta = getattr(c, 'delta', None)
                text = getattr(delta, 'content', '') or ''
                finish_reason_raw = getattr(c, 'finish_reason', None) or finish_reason_raw
                usage_obj = getattr(c, 'usage', None) or getattr(chunk, 'usage', None)

            if usage_obj:
                if isinstance(usage_obj, dict):
                    input_tokens = usage_obj.get('prompt_tokens', input_tokens)
                    output_tokens = usage_obj.get('completion_tokens', output_tokens)
                else:
                    input_tokens = getattr(usage_obj, 'prompt_tokens', input_tokens)
                    output_tokens = getattr(usage_obj, 'completion_tokens', output_tokens)

            if text:
                full_text += text
                delta_event = {
                    'type': 'content_block_delta',
                    'index': 0,
                    'delta': {'type': 'text_delta', 'text': text},
                }
                yield f'event: content_block_delta\ndata: {_json.dumps(delta_event)}\n\n'

        # 如果没拿到 token 数则用字符数粗估
        if output_tokens == 0 and full_text:
            output_tokens = max(1, len(full_text) // 4)

        # content_block_stop
        yield 'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'

        # message_delta（含 stop_reason 和 output usage）
        stop_reason = finish_reason_map.get(finish_reason_raw or 'stop', 'end_turn')
        msg_delta = {
            'type': 'message_delta',
            'delta': {'stop_reason': stop_reason, 'stop_sequence': None},
            'usage': {'output_tokens': output_tokens},
        }
        yield f'event: message_delta\ndata: {_json.dumps(msg_delta)}\n\n'

        # message_stop
        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    # ----------------------------------------------------------------
    # 智能上下文压缩
    # ----------------------------------------------------------------

    async def _create_summarizer(self, db: AsyncSession):
        """
        创建摘要生成回调。

        复用网关的模型解析能力获取供应商密钥，但绕过计费链路（平台承担成本）。
        返回 (summarizer_fn, token_accumulator_dict)。
        """
        from backend.app.llm.core.compressor import context_compressor

        summary_model = context_compressor.config.summary_model
        # 累计 token 使用量（mutable dict 供回调内累加）
        token_acc = {'input_tokens': 0, 'output_tokens': 0}

        # 尝试从网关模型池解析摘要模型的供应商信息
        models, _ = await self._resolve_models(db, summary_model)
        if not models:
            log.warning(f'[智能压缩] 摘要模型 {summary_model} 不可用，尝试直接调用')

            async def fallback_summarizer(content: str, model: str) -> str:
                response = await self.litellm.acompletion(
                    model=model,
                    messages=[{'role': 'user', 'content': content}],
                    max_tokens=25000,
                    temperature=0.3,
                    timeout=60,
                )
                usage = getattr(response, 'usage', None)
                in_t = getattr(usage, 'prompt_tokens', 0) if usage else 0
                out_t = getattr(usage, 'completion_tokens', 0) if usage else 0
                token_acc['input_tokens'] += in_t
                token_acc['output_tokens'] += out_t
                log.info(
                    f'[智能压缩][内部调用] model={model} '
                    f'input_tokens={in_t} output_tokens={out_t}'
                )
                return response.choices[0].message.content

            return fallback_summarizer, token_acc

        model_config, provider = models[0]
        from backend.app.llm.schema.proxy import ChatCompletionRequest, ChatMessage
        dummy_request = ChatCompletionRequest(
            model=model_config.model_name,
            messages=[ChatMessage(role='user', content='placeholder')],
            max_tokens=25000,
            temperature=0.3,
            stream=False,
        )
        base_params = self._build_litellm_params(model_config, provider, dummy_request, timeout=60)

        async def summarizer(content: str, model: str) -> str:
            params = {**base_params}
            params['messages'] = [{'role': 'user', 'content': content}]
            params['stream'] = False

            response = await self.litellm.acompletion(**params)

            usage = getattr(response, 'usage', None)
            in_t = getattr(usage, 'prompt_tokens', 0) if usage else 0
            out_t = getattr(usage, 'completion_tokens', 0) if usage else 0
            token_acc['input_tokens'] += in_t
            token_acc['output_tokens'] += out_t
            log.info(
                f'[智能压缩][内部调用] model={model} '
                f'input_tokens={in_t} output_tokens={out_t}'
            )
            return response.choices[0].message.content

        return summarizer, token_acc

    async def _try_compress_anthropic(
        self,
        db: AsyncSession,
        request: 'AnthropicMessageRequest',
        models_with_providers: list,
        api_key_metadata: dict | None,
        user_id: int = 0,
        api_key_id: int = 0,
        ip_address: str | None = None,
    ):
        """
        尝试对 Anthropic 格式请求进行压缩。
        返回 CompressResult 或 None。
        """
        from backend.app.llm.core.compressor import context_compressor, is_compress_enabled

        if not is_compress_enabled(context_compressor.config, api_key_metadata):
            return None

        first_model = models_with_providers[0][0]
        max_ctx = first_model.max_context_length or 128000

        # 将 Anthropic 消息转为 dict 列表
        messages_dicts = self._anthropic_messages_to_dicts(request.messages)
        system = request.system

        try:
            summarizer, token_acc = await self._create_summarizer(db)
            result = await context_compressor.compress_if_needed(
                messages=messages_dicts,
                system=system,
                max_context_length=max_ctx,
                summarizer=summarizer,
                api_key_metadata=api_key_metadata,
            )
            if result is not None:
                log.info(
                    f'[智能压缩] Anthropic 消息已压缩: {result.original_count}→{result.compressed_count} 条, '
                    f'token: {result.original_tokens}→{result.compressed_tokens}, '
                    f'摘要块: {result.summary_block_count}, 缓存命中: {result.cache_hit}'
                )
                # 记录压缩成本到 DB
                await self._record_compress_usage(
                    db, result=result, token_acc=token_acc,
                    user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
                )
            return result
        except Exception as e:
            log.warning(f'[智能压缩] 压缩失败，降级为原始消息: {e}')
            return None

    async def _try_compress_openai(
        self,
        db: AsyncSession,
        request: 'ChatCompletionRequest',
        models_with_providers: list,
        api_key_metadata: dict | None,
        user_id: int = 0,
        api_key_id: int = 0,
        ip_address: str | None = None,
    ):
        """
        尝试对 OpenAI 格式请求进行压缩。
        返回 CompressResult 或 None。
        """
        from backend.app.llm.core.compressor import context_compressor, is_compress_enabled

        if not is_compress_enabled(context_compressor.config, api_key_metadata):
            return None

        first_model = models_with_providers[0][0]
        max_ctx = first_model.max_context_length or 128000

        # 将 OpenAI 消息转为 dict 列表
        messages_dicts = [msg.model_dump(exclude_none=True) for msg in request.messages]

        # 提取 system（OpenAI 格式中 system 是消息的一部分）
        system = None
        for msg in messages_dicts:
            if msg.get('role') == 'system':
                system = msg.get('content')
                break

        try:
            summarizer, token_acc = await self._create_summarizer(db)
            result = await context_compressor.compress_if_needed(
                messages=messages_dicts,
                system=system,
                max_context_length=max_ctx,
                summarizer=summarizer,
                api_key_metadata=api_key_metadata,
            )
            if result is not None:
                log.info(
                    f'[智能压缩] OpenAI 消息已压缩: {result.original_count}→{result.compressed_count} 条, '
                    f'token: {result.original_tokens}→{result.compressed_tokens}, '
                    f'摘要块: {result.summary_block_count}, 缓存命中: {result.cache_hit}'
                )
                # 记录压缩成本到 DB
                await self._record_compress_usage(
                    db, result=result, token_acc=token_acc,
                    user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
                )
            return result
        except Exception as e:
            log.warning(f'[智能压缩] 压缩失败，降级为原始消息: {e}')
            return None

    async def _record_compress_usage(
        self,
        db: AsyncSession,
        *,
        result,
        token_acc: dict,
        user_id: int,
        api_key_id: int,
        ip_address: str | None = None,
    ) -> None:
        """记录压缩用量到 DB（异步，失败不影响主流程）"""
        try:
            from backend.app.llm.core.compressor import context_compressor
            from backend.app.llm.service.compress_stats_service import compress_stats_service

            await compress_stats_service.record_compression(
                db,
                user_id=user_id,
                api_key_id=api_key_id,
                request_id=f'compress_{int(time.time() * 1000)}',
                summary_model=context_compressor.config.summary_model,
                input_tokens=token_acc.get('input_tokens', 0),
                output_tokens=token_acc.get('output_tokens', 0),
                original_messages=result.original_count,
                compressed_messages=result.compressed_count,
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                summary_blocks=result.summary_block_count,
                cache_hit=result.cache_hit,
                secondary_compression=result.secondary_compression,
                degraded_keep_count=result.degraded_keep_count,
                generation_ms=result.summary_generation_ms,
                ip_address=ip_address,
            )
        except Exception as e:
            log.warning(f'[智能压缩] 记录压缩用量失败，忽略: {e}')

    @staticmethod
    def _anthropic_messages_to_dicts(messages) -> list[dict]:
        """将 Anthropic 消息列表转为 dict 列表"""
        result = []
        for msg in messages:
            d = {'role': msg.role}
            if isinstance(msg.content, str):
                d['content'] = msg.content
            elif isinstance(msg.content, list):
                d['content'] = [
                    b if isinstance(b, dict) else b.model_dump(exclude_none=True)
                    for b in msg.content
                ]
            else:
                d['content'] = str(msg.content)
            result.append(d)
        return result

    @staticmethod
    def _estimate_input_tokens(messages: list, system: str | list | None = None) -> int:
        """
        快速估算输入 token 数（无需调用 tokenizer，按字符数粗算）

        中文字符按 ~1.5 chars/token，ASCII 按 ~4 chars/token
        用加权方式估算，比单一系数更准确
        """
        char_count_cjk = 0
        char_count_ascii = 0

        def _count(text: str) -> None:
            nonlocal char_count_cjk, char_count_ascii
            if not text:
                return
            for ch in text:
                if ord(ch) > 0x2E80:  # CJK 统一表意字符等
                    char_count_cjk += 1
                else:
                    char_count_ascii += 1

        def _extract_text(content) -> None:
            """从 str / list[dict] / list[Pydantic] / dict 中提取文本并计数"""
            if content is None:
                return
            if isinstance(content, str):
                _count(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        _count(block.get('text') or '')
                    elif hasattr(block, 'text'):
                        _count(getattr(block, 'text', None) or '')
                    elif isinstance(block, str):
                        _count(block)
            elif isinstance(content, dict):
                _count(content.get('text') or '')

        if system:
            _extract_text(system)

        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get('content', '')
            elif hasattr(msg, 'content'):
                content = msg.content
            else:
                content = str(msg)

            _extract_text(content)

        # 中文 ~1.5 chars/token, ASCII ~4 chars/token, 加上消息结构开销
        estimated = int(char_count_cjk / 1.5 + char_count_ascii / 4) + len(messages) * 4
        return estimated

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

            # N4: 统一熔断检查 — 被熔断的供应商跳过，但不算最终失败
            if not breaker.allow_request():
                log.info(
                    f'[LLM Gateway] 跳过已熔断供应商: {provider.name} '
                    f'(模型: {model_config.model_name})'
                )
                continue

            # 获取积分费率
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

            # 构建请求参数
            params = self._build_litellm_params(model_config, provider, request, timeout=600)
            params['stream'] = is_streaming
            request_id = usage_tracker.generate_request_id()

            log.info(
                f'[LLM Gateway] 尝试调用模型: {model_config.model_name} '
                f'(供应商: {provider.name})'
                + (f' [别名: {original_alias}]' if original_alias else '')
            )

            # 调试日志：记录请求详情
            self._log_debug_request(params, provider.name, provider.api_base_url)
            llm_debug_log.log_request(
                request_id, params,
                provider_name=provider.name,
                model_name=model_config.model_name,
                api_base=provider.api_base_url or '',
                is_streaming=is_streaming,
            )

            timer = RequestTimer().start()
            try:
                response = await self.litellm.acompletion(**params)
                timer.stop()
                breaker.record_success()

                # 🔍 DEBUG: dump raw response for tool_calls investigation
                try:
                    _fr = None
                    if hasattr(response, 'choices') and response.choices:
                        _c = response.choices[0]
                        _fr = getattr(_c, 'finish_reason', None)
                    if _fr == 'tool_calls':
                        # Dump full response to see where tool_calls went
                        _resp_json = response.json() if hasattr(response, 'json') else str(response)
                        log.warning(f'[LLM Gateway] RAW-RESPONSE-DUMP finish_reason=tool_calls: {_resp_json[:1000]}')
                except Exception as _e:
                    log.error(f'[LLM Gateway] RAW-RESPONSE-DUMP error: {_e}')

                # 调试日志：记录响应详情
                self._log_debug_response(response, is_streaming=is_streaming, elapsed_ms=timer.elapsed_ms)
                if not is_streaming:
                    _usage = getattr(response, 'usage', None) or {}
                    if hasattr(_usage, 'prompt_tokens'):
                        _in_t, _out_t = _usage.prompt_tokens or 0, _usage.completion_tokens or 0
                    elif isinstance(_usage, dict):
                        _in_t, _out_t = _usage.get('prompt_tokens', 0), _usage.get('completion_tokens', 0)
                    else:
                        _in_t = _out_t = 0
                    llm_debug_log.log_response(
                        request_id, response,
                        elapsed_ms=timer.elapsed_ms,
                        provider_name=provider.name,
                        model_name=model_config.model_name,
                        input_tokens=_in_t,
                        output_tokens=_out_t,
                    )

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
                llm_debug_log.log_error(
                    request_id, e,
                    provider_name=provider.name,
                    model_name=model_config.model_name,
                    elapsed_ms=timer.elapsed_ms,
                )

                error_msg = self._get_error_message(e)
                log.warning(
                    f'[LLM Gateway] 模型调用失败: {model_config.model_name} '
                    f'(供应商: {provider.name}, 错误: {error_msg})，尝试下一个...'
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
                    error_message=error_msg,
                    latency_ms=timer.elapsed_ms,
                    is_streaming=is_streaming,
                    ip_address=ip_address,
                )

                continue

        # 所有模型都失败了
        raise LLMGatewayError(f'All models failed. Last error: {last_error}')

    async def _call_with_failover_anthropic(
        self,
        db: AsyncSession,
        *,
        models_with_providers: list[tuple[ModelConfig, ModelProvider]],
        request: 'AnthropicMessageRequest',
        user_id: int,
        api_key_id: int,
        ip_address: str | None = None,
        original_alias: str | None = None,
    ):
        """
        Anthropic 格式的故障转移调用（按优先级尝试多个模型）

        Args:
            db: 数据库会话
            models_with_providers: 模型和供应商列表（按优先级排序）
            request: Anthropic 请求参数
            user_id: 用户 ID
            api_key_id: API Key ID
            ip_address: IP 地址
            original_alias: 原始别名（用于日志）

        Returns:
            成功时返回 (response, model_config, provider, credit_rate, request_id, timer)
        """
        last_error = None
        tried_models = []

        for model_config, provider in models_with_providers:
            breaker = self._get_circuit_breaker(provider.name)

            # N4: 统一熔断检查 — 被熔断的供应商跳过
            if not breaker.allow_request():
                log.info(
                    f'[LLM Gateway] 跳过已熔断供应商: {provider.name} '
                    f'(模型: {model_config.model_name})'
                )
                continue

            tried_models.append(model_config.model_name)

            # 预检：估算输入 token 数，超过模型上下文限制则跳过
            estimated_input_tokens = self._estimate_input_tokens(request.messages, request.system)
            max_ctx = getattr(model_config, 'max_context_length', 0) or 128000
            if estimated_input_tokens > max_ctx:
                log.warning(
                    f'[LLM Gateway] 输入 token 估算 ({estimated_input_tokens:,}) 超过模型 '
                    f'{model_config.model_name} 上下文限制 ({max_ctx:,})，跳过'
                )
                last_error = LLMGatewayError(
                    f'Prompt token count (~{estimated_input_tokens:,}) exceeds '
                    f'model context limit ({max_ctx:,})',
                    code=400,
                )
                continue

            # 获取积分费率
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)

            # 构建请求参数
            params = self._build_anthropic_params(model_config, provider, request, timeout=600)
            params['stream'] = False
            request_id = usage_tracker.generate_request_id()

            log.info(
                f'[LLM Gateway] Anthropic 故障转移尝试模型: {model_config.model_name} '
                f'(供应商: {provider.name})'
                + (f' [别名: {original_alias}]' if original_alias else '')
            )

            # 调试日志：记录请求详情
            self._log_debug_request(params, provider.name, provider.api_base_url)
            llm_debug_log.log_request(
                request_id, params,
                provider_name=provider.name,
                model_name=model_config.model_name,
                api_base=provider.api_base_url or '',
                is_streaming=False,
            )

            timer = RequestTimer().start()
            try:
                # 为自定义模型注册默认价格，避免 LiteLLM passthrough 日志处理器报错
                self.register_model_pricing(model_config.model_name)

                if self._is_anthropic_native(provider.provider_type):
                    # Anthropic 原生协议：直接调用 Messages API
                    response = await self.litellm.anthropic.messages.acreate(**params)
                else:
                    # OpenAI 兼容协议：转换参数后调用 acompletion，再将响应转回 Anthropic 结构
                    log.info(
                        f'[LLM Gateway] 供应商 {provider.name} 类型为 {provider.provider_type}，'
                        f'将 Anthropic 格式请求转换为 OpenAI 格式调用'
                    )
                    openai_params = self._build_openai_params_from_anthropic(params)
                    # 强制 litellm 使用 OpenAI 协议，避免根据模型名自动识别
                    # （如 claude-* 会被识别为 Anthropic 协议，导致 tool_calls 丢失）
                    openai_params['custom_llm_provider'] = 'openai'
                    # 确保 api_base 传递给 litellm（_build_anthropic_params 不含此字段）
                    if provider.api_base_url:
                        openai_params['api_base'] = provider.api_base_url
                    raw_response = await self.litellm.acompletion(**openai_params)
                    response = self._convert_openai_response_to_anthropic(
                        raw_response, model_config.model_name, request_id
                    )

                timer.stop()
                breaker.record_success()

                # 调试日志：记录响应详情
                self._log_debug_response(response, is_streaming=False, elapsed_ms=timer.elapsed_ms)

                # 提取 token 用量
                _usage = getattr(response, 'usage', None)
                _in_tokens = getattr(_usage, 'input_tokens', 0) if _usage else 0
                _out_tokens = getattr(_usage, 'output_tokens', 0) if _usage else 0

                llm_debug_log.log_response(
                    request_id, response,
                    elapsed_ms=timer.elapsed_ms,
                    provider_name=provider.name,
                    model_name=model_config.model_name,
                    input_tokens=_in_tokens,
                    output_tokens=_out_tokens,
                )

                log.info(
                    f'[LLM Gateway] Anthropic 模型调用成功: {model_config.model_name} '
                    f'(耗时: {timer.elapsed_ms}ms)'
                )

                return response, model_config, provider, credit_rate, request_id, timer

            except Exception as e:
                timer.stop()
                breaker.record_failure()
                last_error = e

                # 调试日志：记录错误详情
                self._log_debug_error(e, provider.name, model_config.model_name)
                llm_debug_log.log_error(
                    request_id, e,
                    provider_name=provider.name,
                    model_name=model_config.model_name,
                    elapsed_ms=timer.elapsed_ms,
                )

                error_msg = self._get_error_message(e)
                log.warning(
                    f'[LLM Gateway] Anthropic 模型调用失败: {model_config.model_name} '
                    f'(供应商: {provider.name}, 错误: {error_msg})，尝试下一个...'
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
                    error_message=error_msg,
                    latency_ms=timer.elapsed_ms,
                    is_streaming=False,
                    ip_address=ip_address,
                )

                continue

        # 所有模型都失败了
        raise LLMGatewayError(
            f'All Anthropic models failed. Tried: {tried_models}. Last error: {last_error}'
        )

    async def chat_completion(
        self,
        db: AsyncSession,
        *,
        request: ChatCompletionRequest,
        user_id: int,
        api_key_id: int,
        api_key_metadata: dict | None = None,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
        app_code: str = 'huanxing',
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
        await credit_service.check_credits(db, user_id, app_code=app_code)

        # 统一模型解析：精确匹配 → 别名映射 → 同类型降级
        models_with_providers, original_alias = await self._resolve_models(db, request.model)
        if not models_with_providers:
            raise ModelNotFoundError(request.model)

        # 智能上下文压缩（OpenAI 格式）
        compress_result = await self._try_compress_openai(
            db, request, models_with_providers, api_key_metadata,
            user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
        )
        if compress_result is not None:
            request = request.model_copy(update={
                'messages': [
                    ChatMessage(role=m['role'], content=m['content'])
                    for m in compress_result.messages
                ]
            })

        # 使用故障转移调用（按优先级依次尝试多个模型）
        response, model_config, provider, credit_rate, request_id, timer = \
            await self._call_with_failover(
                db,
                models_with_providers=models_with_providers,
                request=request,
                user_id=user_id,
                api_key_id=api_key_id,
                ip_address=ip_address,
                is_streaming=False,
                original_alias=original_alias,
            )

        # 提取用量信息
        usage = response.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # 计算并扣除积分
        credits_used = credit_service.calculate_credits(
            input_tokens, output_tokens, credit_rate, model_name=model_config.model_name
        )
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
                app_code=app_code,
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
            # 多种方式提取 tool_calls，兼容 litellm 各种返回格式
            finish_reason = choice.get("finish_reason") if hasattr(choice, "get") else getattr(choice, "finish_reason", None)
            
            tool_calls_raw = None
            # 方式1: dict-like .get()
            if hasattr(message, 'get'):
                tool_calls_raw = message.get('tool_calls')
            # 方式2: getattr
            if tool_calls_raw is None:
                tool_calls_raw = getattr(message, 'tool_calls', None)
            # 方式3: dict() 转换后取
            if tool_calls_raw is None and finish_reason == 'tool_calls':
                try:
                    msg_dict = dict(message) if hasattr(message, '__iter__') else {}
                    tool_calls_raw = msg_dict.get('tool_calls')
                except Exception:
                    pass
            # 方式4: model_dump() (pydantic v2)
            if tool_calls_raw is None and finish_reason == 'tool_calls':
                try:
                    msg_dict = message.model_dump() if hasattr(message, 'model_dump') else {}
                    tool_calls_raw = msg_dict.get('tool_calls')
                except Exception:
                    pass
            # 方式5: json() → parse
            if tool_calls_raw is None and finish_reason == 'tool_calls':
                try:
                    import json as _json
                    msg_json = message.json() if hasattr(message, 'json') else '{}'
                    msg_parsed = _json.loads(msg_json)
                    tool_calls_raw = msg_parsed.get('tool_calls')
                except Exception:
                    pass
            
            # 方式6: 从 litellm _hidden_params 获取原始 Anthropic 响应中的 tool_use blocks
            # litellm 在处理大量工具时会丢失 tool_calls，这是终极 fallback
            if tool_calls_raw is None and finish_reason == 'tool_calls':
                try:
                    hidden = getattr(response, '_hidden_params', {}) or {}
                    original_response = hidden.get('original_response')
                    log.warning(
                        f'[LLM Gateway] 方式6 debug: '
                        f'hidden_keys={list(hidden.keys()) if isinstance(hidden, dict) else "not_dict"}, '
                        f'orig_type={type(original_response).__name__}, '
                        f'orig_repr={repr(original_response)[:300] if original_response else "None"}, '
                        f'response_type={type(response).__name__}, '
                        f'response_dir_sample={[a for a in dir(response) if not a.startswith("__")][:20]}'
                    )
                    if original_response and isinstance(original_response, str):
                        import json as _json
                        orig = _json.loads(original_response)
                        # Anthropic 格式: content 数组中的 tool_use blocks
                        content_blocks = orig.get('content', [])
                        tool_use_blocks = [b for b in content_blocks if b.get('type') == 'tool_use']
                        if tool_use_blocks:
                            log.warning(f'[LLM Gateway] 方式6: 从原始 Anthropic 响应提取到 {len(tool_use_blocks)} 个 tool_use blocks')
                            tool_calls_raw = []
                            for block in tool_use_blocks:
                                tc = {
                                    'id': block.get('id', ''),
                                    'type': 'function',
                                    'function': {
                                        'name': block.get('name', ''),
                                        'arguments': _json.dumps(block.get('input', {})),
                                    }
                                }
                                tool_calls_raw.append(tc)
                    elif original_response and hasattr(original_response, 'content'):
                        # 如果是 Anthropic SDK 对象
                        content_blocks = original_response.content
                        tool_use_blocks = [b for b in content_blocks if getattr(b, 'type', '') == 'tool_use']
                        if tool_use_blocks:
                            import json as _json
                            log.warning(f'[LLM Gateway] 方式6b: 从 Anthropic SDK 对象提取到 {len(tool_use_blocks)} 个 tool_use blocks')
                            tool_calls_raw = []
                            for block in tool_use_blocks:
                                tc = {
                                    'id': getattr(block, 'id', ''),
                                    'type': 'function',
                                    'function': {
                                        'name': getattr(block, 'name', ''),
                                        'arguments': _json.dumps(getattr(block, 'input', {})),
                                    }
                                }
                                tool_calls_raw.append(tc)
                except Exception as e:
                    log.error(f'[LLM Gateway] 方式6 提取失败: {e}')
            
            # DEBUG: 详细打印
            log.warning(
                f'[LLM Gateway] tool_calls extraction: '
                f'choice_type={type(choice).__name__}, msg_type={type(message).__name__}, '
                f'finish_reason={finish_reason}, '
                f'tool_calls_raw_type={type(tool_calls_raw).__name__}, '
                f'tool_calls_raw_bool={bool(tool_calls_raw) if tool_calls_raw is not None else "None"}, '
                f'tool_calls_raw_repr={repr(tool_calls_raw)[:200]}'
            )
            
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
            
            # 提取 role 和 content（兼容 dict 和 object）
            msg_role = message.get('role', 'assistant') if hasattr(message, 'get') else getattr(message, 'role', 'assistant')
            msg_content = message.get('content') if hasattr(message, 'get') else getattr(message, 'content', None)
            msg_finish = choice.get('finish_reason') if hasattr(choice, 'get') else getattr(choice, 'finish_reason', None)
            
            choices.append(
                ChatCompletionChoice(
                    index=i,
                    message=ChatMessage(
                        role=msg_role,
                        content=msg_content,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=msg_finish,
                )
            )

        response_model_name = original_alias or model_config.model_name

        return ChatCompletionResponse(
            id=request_id,
            created=int(time.time()),
            model=response_model_name,
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
        api_key_metadata: dict | None = None,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
        app_code: str = 'huanxing',
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
        await credit_service.check_credits(db, user_id, app_code=app_code)

        # 统一模型解析：精确匹配 → 别名映射 → 同类型降级
        models_with_providers, original_alias = await self._resolve_models(db, request.model)
        if not models_with_providers:
            raise ModelNotFoundError(request.model)

        # 智能上下文压缩（OpenAI 格式）
        compress_result = await self._try_compress_openai(
            db, request, models_with_providers, api_key_metadata,
            user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
        )
        if compress_result is not None:
            request = request.model_copy(update={
                'messages': [
                    ChatMessage(role=m['role'], content=m['content'])
                    for m in compress_result.messages
                ]
            })

        last_error = None
        tried_models = []
        has_sent_data = False  # N2: 跟踪是否已向客户端发送过数据

        for model_config, provider in models_with_providers:
            breaker = self._get_circuit_breaker(provider.name)

            # N4: 统一熔断检查
            if not breaker.allow_request():
                log.info(
                    f'[LLM Gateway] 跳过已熔断供应商: {provider.name} '
                    f'(模型: {model_config.model_name})'
                )
                continue

            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)
            response_model_name = original_alias or model_config.model_name
            tried_models.append(model_config.model_name)

            # 构建请求参数
            params = self._build_litellm_params(model_config, provider, request, timeout=600)
            params['stream'] = True
            # P1-3: 请求精确 token 计数
            params['stream_options'] = {'include_usage': True}
            request_id = usage_tracker.generate_request_id()

            log.info(
                f'[LLM Gateway] 流式故障转移尝试模型: {model_config.model_name} '
                f'(供应商: {provider.name})'
                + (f' [别名: {original_alias}]' if original_alias else '')
            )

            # 调试日志：记录请求详情
            self._log_debug_request(params, provider.name, provider.api_base_url)
            llm_debug_log.log_request(
                request_id, params,
                provider_name=provider.name,
                model_name=model_config.model_name,
                api_base=provider.api_base_url or '',
                is_streaming=True,
            )

            timer = RequestTimer().start()
            content_buffer = ''
            # P1-3: 从流式最后一个 chunk 获取精确 token 数
            stream_input_tokens = 0
            stream_output_tokens = 0
            stream_success = False

            try:
                response = await self.litellm.acompletion(**params)

                async for chunk in response:
                    choices = chunk.get('choices', [])

                    # P1-3: 提取精确 usage（最后一个 chunk 可能包含）
                    chunk_usage = chunk.get('usage')
                    if chunk_usage:
                        stream_input_tokens = chunk_usage.get('prompt_tokens', stream_input_tokens)
                        stream_output_tokens = chunk_usage.get('completion_tokens', stream_output_tokens)

                    if not choices:
                        continue

                    delta = choices[0].get('delta', {})
                    content = delta.get('content', '')
                    if content:
                        content_buffer += content

                    # 构建 SSE 数据
                    chunk_data = ChatCompletionChunk(
                        id=request_id,
                        created=int(time.time()),
                        model=response_model_name,
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

                    has_sent_data = True  # N2: 标记已发送数据
                    yield f'data: {chunk_data.model_dump_json()}\n\n'

                # 发送结束标记
                yield 'data: [DONE]\n\n'

                timer.stop()
                breaker.record_success()
                stream_success = True

                # 调试日志：记录流式响应完成
                self._log_debug_response(None, is_streaming=True, elapsed_ms=timer.elapsed_ms)
                llm_debug_log.log_stream_end(
                    request_id,
                    elapsed_ms=timer.elapsed_ms,
                    provider_name=provider.name,
                    model_name=model_config.model_name,
                    input_tokens=stream_input_tokens,
                    output_tokens=stream_output_tokens,
                    full_content=content_buffer[:5000] if content_buffer else '',
                )

                # P1-3: 优先使用精确 token 数，回退到估算
                if stream_input_tokens > 0 or stream_output_tokens > 0:
                    input_tokens = stream_input_tokens
                    output_tokens = stream_output_tokens
                else:
                    input_tokens = len(str(request.messages)) // 4
                    output_tokens = len(content_buffer) // 4

                # 计算并扣除积分
                credits_used = credit_service.calculate_credits(
                    input_tokens, output_tokens, credit_rate, model_name=model_config.model_name
                )
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
                        app_code=app_code,
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

                log.info(
                    f'[LLM Gateway] 流式模型调用成功: {model_config.model_name} '
                    f'(耗时: {timer.elapsed_ms}ms, tokens: in={input_tokens} out={output_tokens})'
                )

                # 成功，直接返回
                return

            except Exception as e:
                timer.stop()
                breaker.record_failure()
                last_error = e

                # 调试日志：记录错误详情
                self._log_debug_error(e, provider.name, model_config.model_name)
                llm_debug_log.log_error(
                    request_id, e,
                    provider_name=provider.name,
                    model_name=model_config.model_name,
                    elapsed_ms=timer.elapsed_ms,
                )

                error_msg = self._get_error_message(e)
                log.warning(
                    f'[LLM Gateway] 流式模型调用失败: {model_config.model_name} '
                    f'(供应商: {provider.name}, 错误: {error_msg})'
                )

                # 记录错误
                await usage_tracker.track_error(
                    db,
                    user_id=user_id,
                    api_key_id=api_key_id,
                    model_id=model_config.id,
                    provider_id=provider.id,
                    request_id=request_id,
                    model_name=model_config.model_name,
                    error_message=error_msg,
                    latency_ms=timer.elapsed_ms,
                    is_streaming=True,
                    ip_address=ip_address,
                )

                # N2: 如果已经发送过数据，不能 failover（会导致客户端收到混乱的响应）
                if has_sent_data:
                    log.error(
                        f'[LLM Gateway] 流式传输中断，已发送部分数据，无法 failover: '
                        f'{model_config.model_name}'
                    )
                    error_data = {'error': {'message': f'Stream interrupted: {error_msg}', 'type': 'stream_error'}}
                    yield f'data: {json.dumps(error_data)}\n\n'
                    return

                log.info(f'[LLM Gateway] 尝试下一个模型...')
                continue

        # 所有模型都失败了
        error_msg = f'All streaming models failed. Tried: {tried_models}. Last error: {last_error}'
        log.error(f'[LLM Gateway] {error_msg}')
        error_data = {'error': {'message': error_msg, 'type': 'gateway_error'}}
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
        timeout: int | None = None,
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

        # 限制 max_tokens 不超过模型配置的最大值
        effective_max_tokens = min(request.max_tokens, model_config.max_tokens)

        params = {
            'model': model_name,
            'messages': messages,
            'max_tokens': effective_max_tokens,
            'api_key': api_key,
            'stream': request.stream,
            'timeout': timeout or 60,  # P0-2: 默认 60 秒超时
        }

        # 设置 API base URL
        if provider.api_base_url:
            params['api_base'] = provider.api_base_url
            # anthropic 官方 SDK (passthrough 模式) 用 base_url，LiteLLM 通用用 api_base
            # 两个都传，确保不同调用路径都能正确使用
            params['base_url'] = provider.api_base_url

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
        api_key_metadata: dict | None = None,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
        app_code: str = 'huanxing',
    ) -> 'AnthropicMessageResponse':
        """
        Anthropic 格式聊天补全（非流式）- 支持故障转移
        
        故障转移策略：
        1. 如果请求的模型是别名，依次尝试别名映射的所有模型
        2. 如果别名映射的模型都失败了，获取同类型的 fallback 模型继续尝试
        3. 如果不是别名，单一模型失败后也会尝试 fallback 模型
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
        await credit_service.check_credits(db, user_id, app_code=app_code)

        # 统一模型解析：精确匹配 → 别名映射 → 同类型降级
        models_with_providers, original_alias = await self._resolve_models(db, request.model)
        if not models_with_providers:
            raise ProviderUnavailableError(
                f'No available models for request: {request.model}'
            )

        log.info(
            f'[LLM Gateway] Anthropic 候选模型列表: '
            f'{[m.model_name for m, _ in models_with_providers]}'
        )

        # 智能上下文压缩（Anthropic 格式）
        compress_result = await self._try_compress_anthropic(
            db, request, models_with_providers, api_key_metadata,
            user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
        )
        if compress_result is not None:
            from backend.app.llm.schema.proxy import AnthropicMessage
            request = request.model_copy(update={
                'messages': [
                    AnthropicMessage(role=m['role'], content=m['content'])
                    for m in compress_result.messages
                ]
            })

        # 使用故障转移调用
        response, model_config, provider, credit_rate, request_id, timer = \
            await self._call_with_failover_anthropic(
                db,
                models_with_providers=models_with_providers,
                request=request,
                user_id=user_id,
                api_key_id=api_key_id,
                ip_address=ip_address,
                original_alias=original_alias,
            )

        response_model_name = original_alias or model_config.model_name

        # 提取用量信息 - LiteLLM 返回的是 Anthropic 格式
        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'input_tokens', 0) if usage else 0
        output_tokens = getattr(usage, 'output_tokens', 0) if usage else 0

        # 计算并扣除积分
        credits_used = credit_service.calculate_credits(
            input_tokens, output_tokens, credit_rate, model_name=model_config.model_name
        )
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
                app_code=app_code,
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

        # 构建压缩元信息
        response_metadata = None
        if compress_result is not None:
            response_metadata = {
                'context_compressed': True,
                'compression': {
                    'original_messages': compress_result.original_count,
                    'compressed_messages': compress_result.compressed_count,
                    'summary_blocks': compress_result.summary_block_count,
                    'original_tokens': compress_result.original_tokens,
                    'compressed_tokens': compress_result.compressed_tokens,
                    'cache_hit': compress_result.cache_hit,
                    'secondary_compression': compress_result.secondary_compression,
                    'degraded_keep_count': compress_result.degraded_keep_count,
                },
            }

        return AnthropicMessageResponse(
            id=getattr(response, 'id', request_id),
            model=response_model_name,
            content=content,
            stop_reason=getattr(response, 'stop_reason', None),
            usage=AnthropicUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            metadata=response_metadata,
        )

    async def prepare_anthropic_stream(
        self,
        db: AsyncSession,
        *,
        request: 'AnthropicMessageRequest',
        user_id: int,
        api_key_id: int,
        api_key_metadata: dict | None = None,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        ip_address: str | None = None,
        app_code: str = 'huanxing',
    ) -> dict[str, Any]:
        """
        准备 Anthropic 流式响应所需的所有信息（在数据库会话内完成）- 支持故障转移
        
        返回一个上下文 dict，包含所有候选模型的信息，用于 execute 阶段的故障转移
        """
        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )
        
        # 检查用户积分
        await credit_service.check_credits(db, user_id, app_code=app_code)
        
        # 统一模型解析：精确匹配 → 别名映射 → 同类型降级
        models_with_providers, original_alias = await self._resolve_models(db, request.model)
        if not models_with_providers:
            raise ProviderUnavailableError(
                f'No available models for request: {request.model}'
            )

        log.info(
            f'[LLM Gateway] Anthropic 流式候选模型列表: '
            f'{[m.model_name for m, _ in models_with_providers]}'
        )

        # 智能上下文压缩（Anthropic 格式）
        compress_result = await self._try_compress_anthropic(
            db, request, models_with_providers, api_key_metadata,
            user_id=user_id, api_key_id=api_key_id, ip_address=ip_address,
        )
        if compress_result is not None:
            from backend.app.llm.schema.proxy import AnthropicMessage
            request = request.model_copy(update={
                'messages': [
                    AnthropicMessage(role=m['role'], content=m['content'])
                    for m in compress_result.messages
                ]
            })

        # 为每个候选模型预先构建参数
        candidates = []
        for model_config, provider in models_with_providers:
            credit_rate = await credit_service.get_model_credit_rate(db, model_config.id)
            params = self._build_anthropic_params(model_config, provider, request, timeout=600)
            params['stream'] = True
            
            candidates.append({
                'params': params,
                'model_config': {
                    'id': model_config.id,
                    'model_name': model_config.model_name,
                    'model_type': model_config.model_type,
                    'max_context_length': model_config.max_context_length,
                    # 保存为字符串以保持精度，在 execute 中转换为 Decimal
                    'input_cost_per_1k': str(model_config.input_cost_per_1k) if model_config.input_cost_per_1k else '0',
                    'output_cost_per_1k': str(model_config.output_cost_per_1k) if model_config.output_cost_per_1k else '0',
                },
                'provider': {
                    'id': provider.id,
                    'name': provider.name,
                    'api_base_url': provider.api_base_url,
                    'provider_type': provider.provider_type,  # 流式阶段分支判断需要
                },
                'credit_rate': credit_rate,
            })

        response_model_name = original_alias or models_with_providers[0][0].model_name
        
        return {
            'candidates': candidates,
            'response_model_name': response_model_name,
            'original_alias': original_alias,
            'user_id': user_id,
            'api_key_id': api_key_id,
            'ip_address': ip_address,
            'app_code': app_code,
            'compress_result': compress_result if compress_result is not None else None,
        }

    async def execute_anthropic_stream(
        self,
        context: dict[str, Any],
    ) -> AsyncIterator[str]:
        """
        执行 Anthropic 流式响应 - 支持故障转移 + 使用量统计
        
        依次尝试 candidates 中的模型，直到成功为止
        使用 LiteLLM 的 anthropic.messages.acreate(stream=True) 接口
        流式结束后记录使用量并扣除积分
        """
        import codecs
        import traceback
        
        candidates = context.get('candidates', [])
        response_model_name = context['response_model_name']
        original_alias = context.get('original_alias')
        user_id = context.get('user_id')
        api_key_id = context.get('api_key_id')
        ip_address = context.get('ip_address')
        app_code = context.get('app_code', 'huanxing')
        compress_result = context.get('compress_result')
        
        # 向后兼容：如果没有 candidates，使用旧格式
        if not candidates and 'params' in context:
            candidates = [{
                'params': context['params'],
                'model_config': context['model_config'],
                'provider': context['provider'],
                'credit_rate': context.get('credit_rate'),
            }]
        
        if not candidates:
            error_event = {
                'type': 'error',
                'error': {
                    'type': 'api_error',
                    'message': 'No available models for streaming',
                }
            }
            yield f'event: error\ndata: {json.dumps(error_event)}\n\n'
            return
        
        last_error = None
        tried_models = []
        has_sent_data = False  # N2: 跟踪是否已向客户端发送过数据
        
        for candidate in candidates:
            params = candidate['params']
            model_config = candidate['model_config']
            provider_info = candidate['provider']
            credit_rate = candidate.get('credit_rate')
            
            model_name = model_config['model_name']
            model_id = model_config['id']
            provider_name = provider_info['name']
            provider_id = provider_info['id']
            api_base_url = provider_info.get('api_base_url')
            
            tried_models.append(model_name)

            # 预检：估算输入 token 数，超过模型上下文限制则跳过
            max_ctx = model_config.get('max_context_length') or 128000
            messages_in_params = params.get('messages', [])
            system_in_params = params.get('system')
            estimated_input_tokens = self._estimate_input_tokens(messages_in_params, system_in_params)
            if estimated_input_tokens > max_ctx:
                log.warning(
                    f'[LLM Gateway] 输入 token 估算 ({estimated_input_tokens:,}) 超过模型 '
                    f'{model_name} 上下文限制 ({max_ctx:,})，跳过'
                )
                last_error = f'Prompt token count (~{estimated_input_tokens:,}) exceeds model context limit ({max_ctx:,})'
                continue

            # 获取熔断器（用于记录成功/失败，不做硬跳过）
            breaker = self._get_circuit_breaker(provider_name)

            log.info(
                f'[LLM Gateway] Anthropic 流式故障转移尝试模型: {model_name} '
                f'(供应商: {provider_name})'
                + (f' [别名: {original_alias}]' if original_alias else '')
            )
            
            if self.debug_mode:
                llm_debug_log.info(f'[DEBUG] Anthropic 流式响应开始 | model: {model_name}')
                self._log_debug_request(params, provider_name, api_base_url)
            
            timer = RequestTimer().start()
            decoder = codecs.getincrementaldecoder('utf-8')('replace')
            request_id = usage_tracker.generate_request_id()
            
            llm_debug_log.log_request(
                request_id, params,
                provider_name=provider_name,
                model_name=model_name,
                api_base=api_base_url or '',
                is_streaming=True,
            )
            
            # 用于收集使用量信息
            input_tokens = 0
            output_tokens = 0
            stream_success = False
            
            try:
                # 为自定义模型注册默认价格，避免 LiteLLM passthrough 日志处理器报错
                self.register_model_pricing(model_name)

                provider_type = provider_info.get('provider_type', 'anthropic')
                use_anthropic_native = self._is_anthropic_native(provider_type)

                if self.debug_mode:
                    proto = 'Anthropic 原生协议' if use_anthropic_native else f'OpenAI 兼容协议 ({provider_type})'
                    llm_debug_log.info(f'[DEBUG] Anthropic 流式 | 使用协议: {proto} | model: {model_name}')

                chunk_count = 0

                if use_anthropic_native:
                    # ── Anthropic 原生流 ──────────────────────────────────────
                    response = await self.litellm.anthropic.messages.acreate(**params)

                    if self.debug_mode:
                        llm_debug_log.info(f'[DEBUG] LiteLLM anthropic 流返回: {type(response)}')

                    async for chunk in response:
                        if self.debug_mode and chunk_count == 0:
                            llm_debug_log.info(f'[DEBUG] 第一个 chunk: {str(chunk)[:300]}')
                        chunk_count += 1

                        # N6: 统一格式化方法（处理 bytes/str/dict/object）
                        if isinstance(chunk, bytes):
                            decoded = decoder.decode(chunk, final=False)
                            if decoded:
                                formatted, input_tokens, output_tokens = self._format_anthropic_chunk(
                                    decoded, input_tokens, output_tokens
                                )
                                if formatted:
                                    if compress_result and not has_sent_data and 'message_start' in formatted:
                                        formatted = self._inject_compress_metadata(formatted, compress_result)
                                    has_sent_data = True
                                    yield formatted
                        else:
                            formatted, input_tokens, output_tokens = self._format_anthropic_chunk(
                                chunk, input_tokens, output_tokens
                            )
                            if formatted:
                                if compress_result and not has_sent_data and 'message_start' in formatted:
                                    formatted = self._inject_compress_metadata(formatted, compress_result)
                                has_sent_data = True
                                yield formatted

                    # 刷新解码器
                    final_decoded = decoder.decode(b'', final=True)
                    if final_decoded:
                        yield final_decoded

                else:
                    # ── OpenAI 兼容协议：转换参数，转换响应流 ───────────────
                    log.info(
                        f'[LLM Gateway] 供应商 {provider_name} 类型为 {provider_type}，'
                        f'将 Anthropic 流式请求转换为 OpenAI 格式调用'
                    )
                    openai_params = self._build_openai_params_from_anthropic(params)
                    openai_params['stream'] = True
                    openai_params['stream_options'] = {'include_usage': True}
                    # 强制 litellm 使用 OpenAI 协议
                    openai_params['custom_llm_provider'] = 'openai'
                    # 确保 api_base 传递给 litellm
                    if api_base_url:
                        openai_params['api_base'] = api_base_url
                    openai_stream = await self.litellm.acompletion(**openai_params)

                    message_id = usage_tracker.generate_request_id()
                    # 注入压缩元信息标记（通过第一个 message_start 事件携带）
                    first_chunk = True
                    async for sse_line in self._convert_openai_stream_to_anthropic_sse(
                        openai_stream, model_name, message_id
                    ):
                        chunk_count += 1
                        if first_chunk and compress_result and 'message_start' in sse_line:
                            sse_line = self._inject_compress_metadata(sse_line, compress_result)
                            first_chunk = False
                        has_sent_data = True
                        # 顺便解析 token 数，供后续计费（message_start/message_delta 事件携带）
                        input_tokens, output_tokens = self._extract_usage_from_sse(
                            sse_line, input_tokens, output_tokens
                        )
                        yield sse_line

                timer.stop()
                breaker.record_success()
                stream_success = True
                
                if self.debug_mode:
                    llm_debug_log.info(
                        f'[DEBUG] Anthropic 流式响应结束 | 模型: {model_name} | '
                        f'共 {chunk_count} 个 chunk | 耗时: {timer.elapsed_ms}ms | '
                        f'tokens: in={input_tokens} out={output_tokens}'
                    )
                
                log.info(
                    f'[LLM Gateway] Anthropic 流式模型调用成功: {model_name} '
                    f'(耗时: {timer.elapsed_ms}ms, tokens: in={input_tokens} out={output_tokens})'
                )
                llm_debug_log.log_stream_end(
                    request_id,
                    elapsed_ms=timer.elapsed_ms,
                    provider_name=provider_name,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    chunk_count=chunk_count,
                )
                
                # 流式成功后，记录使用量和扣除积分
                log.info(
                    f'[LLM Gateway] 准备记录流式使用量: user_id={user_id}, api_key_id={api_key_id}, '
                    f'model_id={model_id}, provider_id={provider_id}'
                )
                if user_id and api_key_id:
                    try:
                        async with async_db_session() as db:
                            # 计算并扣除积分
                            credits_used = credit_service.calculate_credits(
                                input_tokens, output_tokens, credit_rate, model_name=model_name
                            )
                            if credits_used > 0:
                                await credit_service.deduct_credits(
                                    db,
                                    user_id=user_id,
                                    credits=credits_used,
                                    reference_id=request_id,
                                    reference_type='llm_usage',
                                    description=f'模型调用(流式): {model_name}',
                                    extra_data={
                                        'model_name': model_name,
                                        'input_tokens': input_tokens,
                                        'output_tokens': output_tokens,
                                        'streaming': True,
                                    },
                                    app_code=app_code,
                                )
                            
                            # N5: 使用安全版本记录使用量（失败时自动缓冲到 Redis）
                            await usage_tracker.track_success_safe(
                                db,
                                user_id=user_id,
                                api_key_id=api_key_id,
                                model_id=model_id,
                                provider_id=provider_id,
                                request_id=request_id,
                                model_name=model_name,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                input_cost_per_1k=Decimal(model_config.get('input_cost_per_1k', '0')),
                                output_cost_per_1k=Decimal(model_config.get('output_cost_per_1k', '0')),
                                latency_ms=timer.elapsed_ms,
                                is_streaming=True,
                                ip_address=ip_address,
                            )
                            
                            # 消费 tokens (速率限制)
                            await rate_limiter.consume_tokens(api_key_id, input_tokens + output_tokens)
                            
                            log.info(
                                f'[LLM Gateway] 流式使用量已记录: {model_name} | '
                                f'tokens: {input_tokens + output_tokens} | credits: {credits_used}'
                            )
                    except Exception as track_error:
                        import traceback as tb
                        log.error(
                            f'[LLM Gateway] 流式使用量记录失败: {track_error}\n'
                            f'{tb.format_exc()}'
                        )
                        # N5: 兜底 — 至少把用量缓冲到 Redis，确保数据不丢失
                        try:
                            await usage_tracker.track_success_safe(
                                None,
                                user_id=user_id,
                                api_key_id=api_key_id,
                                model_id=model_id,
                                provider_id=provider_id,
                                request_id=request_id,
                                model_name=model_name,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                input_cost_per_1k=Decimal(model_config.get('input_cost_per_1k', '0')),
                                output_cost_per_1k=Decimal(model_config.get('output_cost_per_1k', '0')),
                                latency_ms=timer.elapsed_ms,
                                is_streaming=True,
                                ip_address=ip_address,
                            )
                        except Exception:
                            pass
                else:
                    log.warning(
                        f'[LLM Gateway] 无法记录流式使用量: user_id={user_id}, api_key_id={api_key_id}')
                
                # 成功，直接返回
                return
                
            except Exception as e:
                timer.stop()
                breaker.record_failure()
                last_error = e
                
                error_msg = self._get_error_message(e)
                log.warning(
                    f'[LLM Gateway] Anthropic 流式模型调用失败: {model_name} '
                    f'(供应商: {provider_name}, 错误: {error_msg})'
                )
                
                if self.debug_mode:
                    llm_debug_log.error(f'[DEBUG] Anthropic 流式响应异常: {e}\n{traceback.format_exc()}')
                    self._log_debug_error(e, provider_name, model_name)
                llm_debug_log.log_error(
                    request_id, e,
                    provider_name=provider_name,
                    model_name=model_name,
                    elapsed_ms=timer.elapsed_ms,
                )
                
                # 记录失败
                if user_id and api_key_id:
                    try:
                        async with async_db_session() as db:
                            await usage_tracker.track_error(
                                db,
                                user_id=user_id,
                                api_key_id=api_key_id,
                                model_id=model_id,
                                provider_id=provider_id,
                                request_id=request_id,
                                model_name=model_name,
                                error_message=error_msg,
                                latency_ms=timer.elapsed_ms,
                                is_streaming=True,
                                ip_address=ip_address,
                            )
                    except Exception as track_error:
                        log.error(f'[LLM Gateway] 流式错误记录失败: {track_error}')
                
                # N2: 如果已经发送过数据，不能 failover
                if has_sent_data:
                    log.error(
                        f'[LLM Gateway] Anthropic 流式传输中断，已发送部分数据，无法 failover: '
                        f'{model_name}'
                    )
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'stream_interrupted',
                            'message': f'Stream interrupted: {error_msg}',
                        }
                    }
                    yield f'event: error\ndata: {json.dumps(error_event)}\n\n'
                    return
                
                log.info(f'[LLM Gateway] 尝试下一个模型...')
                continue
        
        # 所有模型都失败了
        error_msg = f'All streaming models failed. Tried: {tried_models}. Last error: {last_error}'
        log.error(f'[LLM Gateway] {error_msg}')
        
        error_event = {
            'type': 'error',
            'error': {
                'type': 'api_error',
                'message': error_msg,
            }
        }
        yield f'event: error\ndata: {json.dumps(error_event)}\n\n'

    @staticmethod
    def _inject_compress_metadata(formatted: str, compress_result) -> str:
        """在 message_start SSE 事件中注入压缩元信息"""
        try:
            # SSE 格式: "event: message_start\ndata: {...}\n\n"
            # 找到 data: 行并解析 JSON
            lines = formatted.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('data: '):
                    data = json.loads(line[6:])
                    if isinstance(data, dict) and data.get('type') == 'message_start':
                        message = data.get('message', {})
                        message.setdefault('metadata', {})
                        message['metadata']['context_compressed'] = True
                        message['metadata']['compression'] = {
                            'original_messages': compress_result.original_count,
                            'compressed_messages': compress_result.compressed_count,
                            'summary_blocks': compress_result.summary_block_count,
                            'original_tokens': compress_result.original_tokens,
                            'compressed_tokens': compress_result.compressed_tokens,
                            'cache_hit': compress_result.cache_hit,
                            'secondary_compression': compress_result.secondary_compression,
                            'degraded_keep_count': compress_result.degraded_keep_count,
                        }
                        data['message'] = message
                        lines[i] = f'data: {json.dumps(data)}'
                    break
            return '\n'.join(lines)
        except Exception:
            return formatted  # 注入失败不影响正常流

    def _format_anthropic_chunk(
        self, chunk: Any, input_tokens: int, output_tokens: int
    ) -> tuple[str | None, int, int]:
        """
        N6: 统一格式化 Anthropic 流式 chunk

        将不同格式的 chunk（bytes/str/dict/object）统一处理，
        返回 (formatted_sse_string, updated_input_tokens, updated_output_tokens)

        Returns:
            tuple: (SSE 格式字符串或 None, 更新后的 input_tokens, 更新后的 output_tokens)
        """
        if isinstance(chunk, str):
            input_tokens, output_tokens = self._extract_usage_from_sse(
                chunk, input_tokens, output_tokens
            )
            return chunk, input_tokens, output_tokens

        if isinstance(chunk, dict):
            chunk_type = chunk.get('type', '')
            if chunk_type == 'message_start':
                message = chunk.get('message', {})
                usage = message.get('usage', {})
                input_tokens = usage.get('input_tokens', input_tokens)
            elif chunk_type == 'message_delta':
                usage = chunk.get('usage', {})
                output_tokens = usage.get('output_tokens', output_tokens)
            return f'event: {chunk_type}\ndata: {json.dumps(chunk)}\n\n', input_tokens, output_tokens

        if hasattr(chunk, 'type'):
            chunk_type = getattr(chunk, 'type', 'unknown')
            if chunk_type == 'message_start' and hasattr(chunk, 'message'):
                message = chunk.message
                if hasattr(message, 'usage'):
                    input_tokens = getattr(message.usage, 'input_tokens', input_tokens)
            elif chunk_type == 'message_delta' and hasattr(chunk, 'usage'):
                output_tokens = getattr(chunk.usage, 'output_tokens', output_tokens)

            if hasattr(chunk, 'model_dump'):
                chunk_dict = chunk.model_dump()
            elif hasattr(chunk, 'dict'):
                chunk_dict = chunk.dict()
            else:
                chunk_dict = {'type': chunk_type, 'data': str(chunk)}
            return f'event: {chunk_type}\ndata: {json.dumps(chunk_dict)}\n\n', input_tokens, output_tokens

        # 未知格式
        return f'data: {json.dumps({"type": "unknown", "content": str(chunk)})}\n\n', input_tokens, output_tokens

    def _extract_usage_from_sse(self, data: str, current_input: int, current_output: int) -> tuple[int, int]:
        """
        从 SSE 数据中提取使用量信息
        
        Anthropic SSE 格式:
        - message_start 事件包含 input_tokens
        - message_delta 事件包含 output_tokens
        """
        import re
        
        input_tokens = current_input
        output_tokens = current_output
        
        try:
            # 查找 message_start 事件中的 input_tokens
            if 'message_start' in data and 'input_tokens' in data:
                match = re.search(r'"input_tokens"\s*:\s*(\d+)', data)
                if match:
                    input_tokens = int(match.group(1))
            
            # 查找 message_delta 事件中的 output_tokens
            if 'message_delta' in data and 'output_tokens' in data:
                match = re.search(r'"output_tokens"\s*:\s*(\d+)', data)
                if match:
                    output_tokens = int(match.group(1))
        except Exception:
            pass
        
        return input_tokens, output_tokens

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


    async def embedding(
        self,
        db: AsyncSession,
        *,
        model_name: str,
        input_text: str | list[str],
        user_id: int,
        api_key_id: int,
        rpm_limit: int,
        daily_limit: int,
        monthly_limit: int,
        encoding_format: str | None = None,
        dimensions: int | None = None,
        ip_address: str | None = None,
        app_code: str = 'huanxing',
    ) -> dict:
        """
        Embedding 调用 — 支持故障转移（N1 修复）

        通过 litellm.aembedding 转发到对应供应商，按优先级依次尝试候选模型
        """
        # 检查速率限制
        await rate_limiter.check_all(
            api_key_id,
            rpm_limit=rpm_limit,
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )

        # 检查用户积分
        await credit_service.check_credits(db, user_id, app_code=app_code)

        # 解析模型
        models_with_providers, original_alias = await self._resolve_models(db, model_name)
        if not models_with_providers:
            raise ModelNotFoundError(model_name)

        last_error = None
        tried_models = []

        for model_config, provider in models_with_providers:
            breaker = self._get_circuit_breaker(provider.name)

            # N4: 统一熔断检查
            if not breaker.allow_request():
                log.info(
                    f'[LLM Gateway] 跳过已熔断供应商: {provider.name} '
                    f'(模型: {model_config.model_name})'
                )
                continue

            tried_models.append(model_config.model_name)

            # 解密 API Key
            api_key = None
            if provider.api_key_encrypted:
                try:
                    api_key = key_encryption.decrypt(provider.api_key_encrypted)
                except Exception as e:
                    log.error(f'[LLM Gateway] 解密供应商 {provider.name} 的 API Key 失败: {e}')
                    continue  # 解密失败，尝试下一个供应商

            # 构建模型名称
            # Embedding 特殊处理：对于自定义 api_base 的 openai 类型供应商，
            # 如果模型名不在 litellm 已知列表中，需要加 openai/ 前缀
            # 让 litellm 知道使用 OpenAI 协议发送请求
            has_custom_api_base = bool(provider.api_base_url)
            litellm_model = self._build_embedding_model_name(
                model_config.model_name, provider.provider_type, has_custom_api_base
            )

            params: dict = {
                'model': litellm_model,
                'input': input_text,
                'api_key': api_key,
                'encoding_format': 'float',
                'timeout': 60,
            }
            if provider.api_base_url:
                params['api_base'] = provider.api_base_url
            if encoding_format:
                params['encoding_format'] = encoding_format
            if dimensions:
                params['dimensions'] = dimensions

            request_id = usage_tracker.generate_request_id()

            log.info(
                f'[LLM Gateway] Embedding 故障转移尝试: model={litellm_model}, '
                f'provider={provider.name}, api_base={provider.api_base_url}'
            )

            timer = RequestTimer().start()
            try:
                response = await self.litellm.aembedding(**params)
                timer.stop()
                breaker.record_success()

                # 记录用量
                usage = getattr(response, 'usage', None)
                prompt_tokens = getattr(usage, 'prompt_tokens', 0) if usage else 0

                try:
                    await usage_tracker.track_success(
                        db,
                        user_id=user_id,
                        api_key_id=api_key_id,
                        model_id=model_config.id,
                        provider_id=provider.id,
                        request_id=request_id,
                        model_name=model_config.model_name,
                        input_tokens=prompt_tokens,
                        output_tokens=0,
                        input_cost_per_1k=Decimal('0'),
                        output_cost_per_1k=Decimal('0'),
                        latency_ms=timer.elapsed_ms,
                        ip_address=ip_address,
                    )
                except Exception as e:
                    log.warning(f'[LLM Gateway] Embedding 用量记录失败(不影响结果): {e}')

                log.info(
                    f'[LLM Gateway] Embedding 调用成功: {model_config.model_name} '
                    f'(耗时: {timer.elapsed_ms}ms)'
                )
                return response

            except Exception as e:
                timer.stop()
                breaker.record_failure()
                last_error = e

                error_msg = self._get_error_message(e)
                log.warning(
                    f'[LLM Gateway] Embedding 调用失败: {model_config.model_name} '
                    f'(供应商: {provider.name}, 错误: {error_msg})，尝试下一个...'
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
                    error_message=error_msg,
                    latency_ms=timer.elapsed_ms,
                    is_streaming=False,
                    ip_address=ip_address,
                )

                continue

        # 所有模型都失败了
        raise LLMGatewayError(
            f'All embedding models failed. Tried: {tried_models}. Last error: {last_error}'
        )


# 创建全局网关实例
llm_gateway = LLMGateway()
