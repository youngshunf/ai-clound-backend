"""代理 API - OpenAI/Anthropic 兼容
@author Ysf

认证方式：
- 桌面端使用 x-api-key header 传递 LLM Token (sk-cf-xxx)
- API Key 同时用于身份认证和用量追踪
- 不需要 JWT Token，简化桌面端集成
"""

from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from backend.app.llm.core.rate_limiter import RateLimitExceeded
from backend.app.llm.schema.proxy import (
    AnthropicCountTokensRequest,
    AnthropicMessageRequest,
    AnthropicMessageResponse,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from backend.app.llm.service.gateway_service import gateway_service
from backend.app.user_tier.service.credit_service import InsufficientCreditsError
from backend.common.exception import errors
from backend.common.exception.errors import HTTPError
from backend.common.log import log
from backend.database.db import CurrentSession
from backend.utils.serializers import MsgSpecJSONResponse

router = APIRouter()


@router.api_route(
    '/v1/models',
    methods=['GET', 'HEAD'],
    summary='Anthropic 兼容模型列表',
    description='兼容 Anthropic /v1/models 端点，用于服务可用性检查',
)
async def anthropic_models(request: Request, db: CurrentSession):
    """
    Anthropic 兼容的模型列表接口
    
    - HEAD 请求：用于检查服务可用性（无需返回 body）
    - GET 请求：返回可用模型列表
    
    返回格式符合 Anthropic API 规范
    """
    from backend.app.llm.service.model_service import model_service
    
    # HEAD 请求只需要返回状态码，不需要 body
    if request.method == 'HEAD':
        return MsgSpecJSONResponse(content={})
    
    # GET 请求返回模型列表
    models_data = await model_service.get_available_models(db)
    
    # 转换为 Anthropic 格式
    anthropic_models = []
    for model in models_data:
        anthropic_models.append({
            'id': model.get('model_name') or model.get('id'),
            'display_name': model.get('display_name') or model.get('model_name'),
            'created_at': model.get('created_at', '2024-01-01T00:00:00Z'),
            'type': 'model',
        })
    
    return {
        'data': anthropic_models,
        'has_more': False,
        'first_id': anthropic_models[0]['id'] if anthropic_models else None,
        'last_id': anthropic_models[-1]['id'] if anthropic_models else None,
    }


def _get_client_ip(request: Request) -> str | None:
    """获取客户端 IP"""
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else None


@router.post(
    '/v1/chat/completions',
    summary='OpenAI 兼容聊天补全',
    description='兼容 OpenAI Chat Completions API 格式，使用 x-api-key 认证',
    response_model=ChatCompletionResponse,
    response_model_exclude_none=True,
)
async def chat_completions(
    request: Request,
    db: CurrentSession,
    body: ChatCompletionRequest,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
) -> ChatCompletionResponse | StreamingResponse:
    log.info(f'[Proxy API] 收到 OpenAI 格式请求: /v1/chat/completions, model={body.model}, stream={body.stream}')
    ip_address = _get_client_ip(request)

    if body.stream:
        return StreamingResponse(
            gateway_service.chat_completion_stream(
                db,
                api_key=x_api_key,
                request=body,
                ip_address=ip_address,
            ),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )

    return await gateway_service.chat_completion(
        db,
        api_key=x_api_key,
        request=body,
        ip_address=ip_address,
    )


def _get_anthropic_error_type(error_type: str) -> str:
    """将内部错误类型转换为 Anthropic 标准错误类型"""
    # Anthropic 标准错误类型
    standard_types = {
        'invalid_request_error',
        'authentication_error',
        'permission_error',
        'not_found_error',
        'rate_limit_error',
        'api_error',
        'overloaded_error',
    }

    # 映射内部错误类型到 Anthropic 标准错误类型
    internal_to_anthropic = {
        RateLimitExceeded.RPM_EXCEEDED: 'rate_limit_error',
        RateLimitExceeded.DAILY_EXCEEDED: 'rate_limit_error',
        RateLimitExceeded.MONTHLY_EXCEEDED: 'rate_limit_error',
    }

    if error_type in standard_types:
        return error_type
    return internal_to_anthropic.get(error_type, 'api_error')


def _anthropic_error_response(status_code: int, error_type: str, message: str) -> MsgSpecJSONResponse:
    """返回 Anthropic SDK 期望的错误格式（用于非流式请求）

    格式：{"type": "error", "error": {"type": "...", "message": "..."}}
    """
    return MsgSpecJSONResponse(
        status_code=status_code,
        content={
            'type': 'error',
            'error': {
                'type': _get_anthropic_error_type(error_type),
                'message': message,
            },
        },
    )


def _anthropic_stream_error_response(status_code: int, error_type: str, message: str) -> MsgSpecJSONResponse:
    """返回 Anthropic SDK 期望的错误格式（用于流式请求）
    
    重要：即使是流式请求，在准备阶段发生的错误也应该返回 JSON 错误响应，
    而不是 SSE 流。这样 SDK 才能正确解析 HTTP 状态码和错误信息。
    
    格式：{"type": "error", "error": {"type": "...", "message": "..."}}
    """
    return MsgSpecJSONResponse(
        status_code=status_code,
        content={
            'type': 'error',
            'error': {
                'type': _get_anthropic_error_type(error_type),
                'message': message,
            },
        },
        # 明确设置 Content-Type 为 application/json
        # Anthropic SDK 在收到非 2xx 状态码时会解析 JSON 错误信息
        headers={'Content-Type': 'application/json'},
    )


@router.post(
    '/v1/messages',
    summary='Anthropic 兼容消息',
    description='兼容 Anthropic Messages API 格式，使用 x-api-key 认证',
    responses={
        200: {'model': AnthropicMessageResponse},
        401: {'description': 'Authentication error'},
        402: {'description': 'Insufficient credits'},
        404: {'description': 'Resource not found'},
        429: {'description': 'Rate limit exceeded'},
        500: {'description': 'Internal server error'},
    },
)
async def anthropic_messages(
    request: Request,
    db: CurrentSession,
    body: AnthropicMessageRequest,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
):
    log.info(f'[Proxy API] 收到 Anthropic 格式请求: /v1/messages, model={body.model}, stream={body.stream}')
    # [DEBUG] Inspect API Key format
    masked_key = f"{x_api_key[:10]}... ({len(x_api_key)} chars)" if x_api_key else "None"
    log.info(f"[DEBUG] Proxy received Key: {masked_key}")
    
    ip_address = _get_client_ip(request)

    # 根据是否流式请求选择错误响应函数
    error_response_fn = _anthropic_stream_error_response if body.stream else _anthropic_error_response
    
    try:
        if body.stream:
            # 在数据库会话内提前准备好所有数据
            stream_context = await gateway_service.prepare_anthropic_stream(
                db,
                api_key=x_api_key,
                request=body,
                ip_address=ip_address,
            )
            # 流式响应不需要数据库
            return StreamingResponse(
                gateway_service.execute_anthropic_stream(stream_context),
                media_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no',
                },
            )

        return await gateway_service.anthropic_messages(
            db,
            api_key=x_api_key,
            request=body,
            ip_address=ip_address,
        )
    except RateLimitExceeded as e:
        log.warning(f'[Proxy API] Anthropic 请求被限流: {e.detail}')
        return error_response_fn(429, e.error_type, str(e.detail))
    except InsufficientCreditsError as e:
        # 积分不足，映射为 Anthropic 的 rate_limit_error，便于 SDK 统一处理配额问题
        message = getattr(e, 'detail', None) or getattr(e, 'msg', None) or str(e)
        log.warning(f'[Proxy API] Anthropic 请求积分不足: {message}')
        return error_response_fn(402, 'rate_limit_error', message)
    except errors.AuthorizationError as e:
        # API Key 无效或已失效
        message = getattr(e, 'msg', None) or str(e)
        log.warning(f'[Proxy API] Anthropic 请求认证失败: {message}')
        return error_response_fn(401, 'authentication_error', message)
    except errors.NotFoundError as e:
        # 资源不存在（例如 API Key / 模型等）
        message = getattr(e, 'msg', None) or str(e)
        log.warning(f'[Proxy API] Anthropic 请求资源不存在: {message}')
        return error_response_fn(404, 'not_found_error', message)
    except HTTPError as e:
        # 其他 HTTPError（例如网关错误、供应商不可用等），统一映射为 api_error
        message = getattr(e, 'detail', None) or str(e)
        log.error(f'[Proxy API] Anthropic HTTP 错误: {message}')
        return error_response_fn(e.status_code, 'api_error', message)
    except Exception as e:
        # 未处理的异常，返回通用 api_error，保证 SDK 能解析
        log.exception(f'[Proxy API] Anthropic 未处理异常: {e}')
        return error_response_fn(500, 'api_error', 'Internal server error')


@router.post(
    '/v1/messages/count_tokens',
    summary='Anthropic Token 计数',
    description='Anthropic Messages API 的 token 计数接口',
)
async def count_tokens(
    body: AnthropicCountTokensRequest,
) -> dict:
    """
    计算消息的 token 数量
    
    使用 LiteLLM 的 token_counter 功能
    """
    import litellm
    
    # 构建消息列表用于 token 计数
    messages = []
    
    # 添加系统消息
    if body.system:
        system_content = body.system
        if isinstance(system_content, list):
            text_parts = []
            for block in system_content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text_parts.append(block.get('text', ''))
            system_content = '\n'.join(text_parts)
        messages.append({'role': 'system', 'content': system_content})
    
    # 添加用户消息
    for msg in body.messages:
        content = msg.content
        if isinstance(content, list):
            # 提取文本内容
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text_parts.append(block.get('text', ''))
                elif hasattr(block, 'type') and block.type == 'text':
                    text_parts.append(getattr(block, 'text', ''))
            content = '\n'.join(text_parts)
        messages.append({'role': msg.role, 'content': content})
    
    # 使用 LiteLLM 计算 token 数
    # 尝试使用 Anthropic 的 tokenizer，如果不可用则回退到默认
    try:
        input_tokens = litellm.token_counter(
            model=body.model,
            messages=messages,
        )
    except Exception:
        # 回退到粗略估算
        total_text = ''.join(msg.get('content', '') for msg in messages)
        input_tokens = len(total_text) // 4
    
    return {
        'input_tokens': input_tokens,
    }


@router.post(
    '/api/event_logging/batch',
    summary='Anthropic 事件日志',
    description='Anthropic SDK 遥测接口，接受并忽略事件日志',
)
async def event_logging_batch() -> dict:
    """接受 Anthropic SDK 发送的事件日志，直接返回成功"""
    return {'status': 'ok'}
