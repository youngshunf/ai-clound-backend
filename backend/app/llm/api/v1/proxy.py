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

from backend.app.llm.schema.proxy import (
    AnthropicCountTokensRequest,
    AnthropicMessageRequest,
    AnthropicMessageResponse,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from backend.app.llm.service.gateway_service import gateway_service
from backend.common.log import log
from backend.database.db import CurrentSession

router = APIRouter()


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


@router.post(
    '/v1/messages',
    summary='Anthropic 兼容消息',
    description='兼容 Anthropic Messages API 格式，使用 x-api-key 认证',
    response_model=AnthropicMessageResponse,
    response_model_exclude_none=True,
)
async def anthropic_messages(
    request: Request,
    db: CurrentSession,
    body: AnthropicMessageRequest,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
) -> AnthropicMessageResponse | StreamingResponse:
    log.info(f'[Proxy API] 收到 Anthropic 格式请求: /v1/messages, model={body.model}, stream={body.stream}')
    ip_address = _get_client_ip(request)

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
