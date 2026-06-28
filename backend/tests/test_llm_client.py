"""统一 LLM 客户端单测：纯解析/模型链（无网络）+ 传输重试/fallback（httpx MockTransport）。

MockTransport 是传输层测试替身（拦截 HTTP），不是业务 mock——验证客户端自身的重试、模型
fallback、SSE/JSON 解析与 ``trust_env=False`` 契约，不伪造任何 LLM 业务数据。
"""

from __future__ import annotations

import httpx
import pytest

from backend.common.llm import LLMChatClient, LLMError


def _client(handler, **kw) -> LLMChatClient:
    return LLMChatClient(
        base_url='http://gw.local', api_key='sk-test', transport=httpx.MockTransport(handler), **kw
    )


# ---- 配置解析 ----


def test_base_url_appends_v1():
    assert LLMChatClient(base_url='http://gw.local', api_key='k').base_url == 'http://gw.local/v1'
    assert LLMChatClient(base_url='http://gw.local/v1', api_key='k').base_url == 'http://gw.local/v1'
    assert LLMChatClient(base_url='http://gw.local/', api_key='k').base_url == 'http://gw.local/v1'


def test_base_url_empty_raises():
    with pytest.raises(LLMError):
        _ = LLMChatClient(base_url='', api_key='k').base_url


def test_is_configured():
    assert LLMChatClient(base_url='http://x', api_key='k').is_configured is True
    assert LLMChatClient(base_url='', api_key='k').is_configured is False
    assert LLMChatClient(base_url='http://x', api_key='').is_configured is False


def test_model_chain():
    c = LLMChatClient(base_url='http://x', api_key='k', model='m1', fallback_model='m2')
    assert c._model_chain(None, None) == ['m1', 'm2']
    assert c._model_chain('over', None) == ['over']  # per-call 覆盖丢掉 fallback
    assert c._model_chain(None, ['a', 'b']) == ['a', 'b']  # 显式链优先
    dedup = LLMChatClient(base_url='http://x', api_key='k', model='same', fallback_model='same')
    assert dedup._model_chain(None, None) == ['same']


def test_default_model_chain_uses_settings_failover():
    """无 per-instance/per-call 模型的客户端默认走 settings.LLM_DEFAULT_MODELS 整条 failover 链。"""
    from backend.core.conf import settings

    configured = [m for m in settings.LLM_DEFAULT_MODELS if m]
    assert len(configured) >= 2, '默认应配多模型 failover 链（owner 记忆合并等后端任务靠它兜底）'
    c = LLMChatClient(base_url='http://x', api_key='k')
    assert c._model_chain(None, None) == configured


def test_instance_config_beats_settings_chain():
    """实例显式 models / model 永远赢过全局默认链（不被全局链覆盖）。"""
    assert LLMChatClient(base_url='http://x', api_key='k', models=['only'])._model_chain(None, None) == ['only']
    assert LLMChatClient(base_url='http://x', api_key='k', model='solo')._model_chain(None, None) == ['solo']


def test_module_singleton_carries_failover_chain():
    """模块单例 llm_client（owner 记忆合并 / 画像判定走它）默认带 failover 链。"""
    from backend.common.llm import llm_client
    from backend.core.conf import settings

    assert llm_client._model_chain(None, None) == [m for m in settings.LLM_DEFAULT_MODELS if m]


# ---- 纯解析 ----


def test_extract_content_message_and_delta():
    result = {'choices': [{'message': {'content': 'a'}}, {'delta': {'content': 'b'}}]}
    assert LLMChatClient._extract_content(result) == 'ab'
    assert LLMChatClient._extract_content({'choices': []}) is None
    assert LLMChatClient._extract_content({}) is None


def test_parse_sse():
    text = (
        'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        'data: [DONE]\n'
    )
    assert LLMChatClient._extract_content(LLMChatClient._parse_sse(text)) == 'hello'


def test_parse_json_object():
    assert LLMChatClient._parse_json_object('```json\n{"a": 1}\n```') == {'a': 1}
    assert LLMChatClient._parse_json_object('prefix {"a": 2} suffix') == {'a': 2}
    with pytest.raises(LLMError):
        LLMChatClient._parse_json_object('no json here')


# ---- 传输：重试 / fallback / 硬错误 ----


@pytest.fixture
def _no_backoff(monkeypatch):
    import backend.common.llm.client as mod

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(mod.asyncio, 'sleep', _instant)


@pytest.mark.asyncio
async def test_complete_retries_then_succeeds(_no_backoff):
    calls = {'n': 0}

    def handler(_request):
        calls['n'] += 1
        if calls['n'] == 1:
            return httpx.Response(503, text='busy')  # 瞬时错误 → 退避重试
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    out = await _client(handler).complete([{'role': 'user', 'content': 'hi'}])
    assert out == 'ok'
    assert calls['n'] == 2


@pytest.mark.asyncio
async def test_complete_model_fallback(_no_backoff):
    seen: list[str] = []

    def handler(request):
        import json

        model = json.loads(request.content)['model']
        seen.append(model)
        if model == 'm1':
            return httpx.Response(500, text='err')  # 主模型 5xx 穷尽 → 切 fallback
        return httpx.Response(200, json={'choices': [{'message': {'content': 'second'}}]})

    out = await _client(handler, model='m1', fallback_model='m2').complete([{'role': 'user', 'content': 'hi'}])
    assert out == 'second'
    assert 'm1' in seen and 'm2' in seen


@pytest.mark.asyncio
async def test_complete_default_chain_failover(_no_backoff):
    """无 per-call 模型时走全局默认 failover 链：前两个模型 5xx 穷尽后自动切到第三个成功。

    这正是 owner 记忆合并的实际路径（merge 调 llm_client.complete 不带 model）——证明默认
    单模型挂了会沿链自动切换，而不是直接抛 LLMError 把贡献永久搁置。
    """
    from backend.core.conf import settings

    chain = [m for m in settings.LLM_DEFAULT_MODELS if m]
    assert len(chain) >= 3
    seen: list[str] = []

    def handler(request):
        import json

        model = json.loads(request.content)['model']
        seen.append(model)
        if model in chain[:2]:
            return httpx.Response(500, text='gateway rejects this model')  # 前两模型全挂
        return httpx.Response(200, json={'choices': [{'message': {'content': 'third'}}]})

    out = await _client(handler).complete([{'role': 'user', 'content': 'hi'}])
    assert out == 'third'
    assert list(dict.fromkeys(seen)) == chain[:3]  # 按链顺序逐个尝试到第三个


@pytest.mark.asyncio
async def test_complete_sse_response(_no_backoff):
    def handler(_request):
        body = 'data: {"choices":[{"delta":{"content":"流式"}}]}\n\ndata: [DONE]\n'
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    out = await _client(handler).complete([{'role': 'user', 'content': 'hi'}])
    assert out == '流式'


@pytest.mark.asyncio
async def test_complete_failover_on_hard_error(_no_backoff):
    """硬错误（非瞬时 4xx，如 model_not_found / 余额不足）也触发模型 failover，不再直接抛。

    这正是「一个模型挂了，自动切换下一个」：链上某模型返回 4xx 硬错误时切下一个，整条链
    穷尽才抛 LLMError（修 gpt-5.5 余额不足直接卡死、不切换的问题）。
    """
    seen: list[str] = []

    def handler(request):
        import json

        model = json.loads(request.content)['model']
        seen.append(model)
        if model == 'm1':
            return httpx.Response(404, text='model_not_found')  # 硬错误（非瞬时）→ 应切下一个
        return httpx.Response(200, json={'choices': [{'message': {'content': 'recovered'}}]})

    out = await _client(handler, models=['m1', 'm2']).complete([{'role': 'user', 'content': 'hi'}])
    assert out == 'recovered'
    assert seen == ['m1', 'm2']  # m1 硬错误后切到 m2


@pytest.mark.asyncio
async def test_complete_all_models_hard_error_raises(_no_backoff):
    def handler(_request):
        return httpx.Response(400, text='bad request')  # 全链非瞬时硬错误 → 穷尽后抛

    with pytest.raises(LLMError):
        await _client(handler, models=['m1', 'm2']).complete([{'role': 'user', 'content': 'hi'}])


@pytest.mark.asyncio
async def test_complete_json(_no_backoff):
    def handler(_request):
        return httpx.Response(200, json={'choices': [{'message': {'content': '```json\n{"k": "v"}\n```'}}]})

    out = await _client(handler).complete_json([{'role': 'user', 'content': 'hi'}])
    assert out == {'k': 'v'}
