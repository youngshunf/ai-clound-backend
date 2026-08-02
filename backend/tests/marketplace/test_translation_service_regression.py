"""marketplace 翻译服务回归测试 —— 抽公共件（backend/common/translation/）前后行为必须一致。

写在抽取**之前**：轨道 B 要把 `translate_markdown` / `detect_language` / 长文分段
搬进 `backend/common/translation/`，这批用例钉住 marketplace 侧现有的可观察行为，
抽完再跑一遍，两边都绿才算没改坏（施工清单「风险与预案」第一条）。

LLM 走 `httpx.MockTransport`（传输层替身，拦 HTTP，不伪造业务数据），与
`backend/tests/test_llm_client.py` 同一范式。
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.marketplace.service.translation_service import TranslationService


def _svc_with_gateway(handler) -> TranslationService:
    """构造一个 LLM 走 MockTransport 的 TranslationService。"""
    from backend.common.llm import LLMChatClient

    svc = TranslationService()
    svc._llm = LLMChatClient(
        base_url='http://gw.local',
        api_key='sk-test',
        model='test-model',
        transport=httpx.MockTransport(handler),
    )
    return svc


def _ok(content: str) -> httpx.Response:
    """构造一个 OpenAI 兼容的非流式成功响应。"""
    return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})


# ---- 语言检测 ----


def test_detect_language_zh_en_unknown() -> None:
    svc = TranslationService()
    assert svc.detect_language('这是一段中文内容，用于语言检测。') == 'zh'
    assert svc.detect_language('This is an English sentence used for detection.') == 'en'
    assert svc.detect_language('') == 'unknown'
    assert svc.detect_language('   ') == 'unknown'


def test_detect_language_falls_back_to_han_script() -> None:
    """langdetect 对极短文本会抛异常，此时按汉字兜底判 zh。"""
    svc = TranslationService()
    assert svc.detect_language('中') == 'zh'


# ---- 长文分段（按段落边界，不切开代码围栏）----


def test_split_markdown_keeps_short_doc_as_one_chunk() -> None:
    text = '# 标题\n\n一段正文。'
    assert TranslationService._split_markdown_for_translation(text) == [text]


def test_split_markdown_splits_on_paragraph_boundary() -> None:
    paras = [f'第 {i} 段。' + '内容' * 200 for i in range(6)]
    text = '\n\n'.join(paras)
    chunks = TranslationService._split_markdown_for_translation(text, budget=800)
    assert len(chunks) > 1
    # 拼回去必须与原文等价（只在段落边界切）
    assert '\n\n'.join(chunks) == text


def test_split_markdown_never_breaks_inside_fenced_code() -> None:
    """代码围栏内不得被切开——切开会让两半各自成为非法 Markdown，翻译必然破坏结构。"""
    code_body = '\n\n'.join(f'line_{i} = {i}' for i in range(60))
    text = f'开头段落。\n\n```python\n{code_body}\n```\n\n结尾段落。'
    chunks = TranslationService._split_markdown_for_translation(text, budget=200)
    for chunk in chunks:
        assert chunk.count('```') % 2 == 0, f'代码围栏被切开: {chunk[:80]!r}'


# ---- translate_markdown 的零 fake 契约 ----


@pytest.mark.asyncio
async def test_translate_markdown_same_language_returns_input_unchanged() -> None:
    svc = TranslationService()
    text = '# Title\n\nBody.'
    assert await svc.translate_markdown(text, 'en', 'en') == text


@pytest.mark.asyncio
async def test_translate_markdown_empty_returns_none() -> None:
    svc = TranslationService()
    assert await svc.translate_markdown('', 'zh', 'en') is None
    assert await svc.translate_markdown('   ', 'zh', 'en') is None


@pytest.mark.asyncio
async def test_translate_markdown_returns_none_on_gateway_failure() -> None:
    """网关穷尽仍失败 → 返回 None，**不回落原文伪装成译文**（零 fake 铁律）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={'error': 'boom'})

    svc = _svc_with_gateway(handler)
    assert await svc.translate_markdown('# 标题\n\n正文。', 'zh', 'en') is None


@pytest.mark.asyncio
async def test_translate_markdown_returns_translation_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok('# Title\n\nBody.')

    svc = _svc_with_gateway(handler)
    assert await svc.translate_markdown('# 标题\n\n正文。', 'zh', 'en') == '# Title\n\nBody.'


@pytest.mark.asyncio
async def test_translate_markdown_joins_chunks_with_blank_line() -> None:
    """多块翻译按 '\\n\\n' 拼回，保留段落结构。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body['messages'][-1]['content'])
        return _ok(f'CHUNK{len(calls)}')

    # 每段约 1000 字 × 8 段，稳超 translate_markdown 内部 3500 字的分段阈值
    paras = ['第 %d 段。%s' % (i, '内容' * 500) for i in range(8)]
    svc = _svc_with_gateway(handler)
    out = await svc.translate_markdown('\n\n'.join(paras), 'zh', 'en')
    assert len(calls) > 1, '长文应被分段成多次调用'
    assert out == '\n\n'.join(f'CHUNK{i + 1}' for i in range(len(calls)))


@pytest.mark.asyncio
async def test_translate_markdown_one_failed_chunk_aborts_whole_doc() -> None:
    """任一分块硬失败 → 整篇放弃返回 None，不产出半成品。"""
    state = {'n': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state['n'] += 1
        if state['n'] == 1:
            return _ok('CHUNK1')
        return httpx.Response(400, json={'error': 'nope'})

    paras = ['第 %d 段。%s' % (i, '内容' * 500) for i in range(8)]
    svc = _svc_with_gateway(handler)
    assert await svc.translate_markdown('\n\n'.join(paras), 'zh', 'en') is None
