"""`backend/common/translation/` 公共件单测：语言检测、结构遮罩、分段、术语表、翻译器。

LLM 走 `httpx.MockTransport`（传输层替身，与 tests/test_llm_client.py 同范式），不伪造业务数据。
"""

from __future__ import annotations

import json

from pathlib import Path

import httpx
import pytest

from backend.common.llm import LLMChatClient
from backend.common.translation import (
    ContentTranslator,
    MarkdownStructureError,
    TranslationError,
    detect_binary_language,
    detect_language,
    glossary_prompt_block,
    glossary_terms,
    is_same_language,
    language_name,
    mask_protected,
    normalize_language,
    restore_protected,
    source_hash,
    split_long_text,
)

# ============================ 语言检测 ============================


def test_detect_binary_language_matches_marketplace_contract() -> None:
    assert detect_binary_language('这是一段中文内容，用于语言检测。') == 'zh'
    assert detect_binary_language('This is an English sentence used for detection.') == 'en'
    assert detect_binary_language('') == 'unknown'
    assert detect_binary_language('中') == 'zh'


def test_detect_language_multilingual() -> None:
    assert detect_language('这是一段中文内容，用于语言检测。') == 'zh'
    assert detect_language('This is an English sentence used for detection.') == 'en'
    # 假名是硬判据：日文里混汉字也不会被误判成中文
    assert detect_language('これは日本語のテキストです。翻訳のテスト。') == 'ja'
    assert detect_language('이것은 한국어 문장입니다.') == 'ko'


def test_detect_language_returns_empty_when_unknown() -> None:
    """判不出返回空串而不是瞎猜一个语言（零 fake：宁可说不知道）。"""
    assert detect_language('') == ''
    assert detect_language('   ') == ''


def test_detect_language_traditional_chinese() -> None:
    assert detect_language('這樣的繁體中文內容應該被判為臺灣正體。') == 'zh-TW'


def test_normalize_language_aliases() -> None:
    assert normalize_language('zh-CN') == 'zh'
    assert normalize_language('zh_Hans') == 'zh'
    assert normalize_language('zh-TW') == 'zh-TW'
    assert normalize_language('zh-HK') == 'zh-TW'
    assert normalize_language('EN-US') == 'en'
    assert normalize_language('') == ''


def test_is_same_language_normalizes_before_compare() -> None:
    assert is_same_language('zh-CN', 'zh') is True
    assert is_same_language('zh', 'zh-TW') is False
    assert is_same_language('', 'zh') is False


def test_language_name_for_prompt() -> None:
    assert language_name('ja') == 'Japanese'
    assert language_name('zh-TW') == 'Traditional Chinese'
    assert language_name('zh') == 'Simplified Chinese'


# ============================ 结构遮罩 ============================


def test_mask_and_restore_roundtrip_is_lossless() -> None:
    text = (
        '看看这个 @张三 写的 #唤星# 帖子，代码在 `foo()` 里：\n\n'
        '```python\nprint("hello")\n```\n\n'
        '详情 https://example.com/a?b=1 或 hasn://deck/d_123'
    )
    masked, fragments = mask_protected(text)
    assert fragments, '应至少遮住若干片段'
    assert restore_protected(masked, fragments) == text


def test_mask_protects_code_url_mention_topic_and_hasn_uri() -> None:
    text = (
        '@李四 说 #话题# 见 `inline` 与 https://a.example/x 和 hasn://community/posts/p_1\n\n'
        '```js\nconst a = 1;\n```'
    )
    masked, fragments = mask_protected(text)
    joined = '\n'.join(fragments)
    assert '@李四' in joined
    assert '#话题#' in joined
    assert '`inline`' in joined
    assert 'https://a.example/x' in joined
    assert 'hasn://community/posts/p_1' in joined
    assert '```js' in joined
    # 遮罩后的正文里这些片段都不该再出现（否则模型还是会翻到它们）
    for fragment in ('@李四', 'https://a.example/x', 'hasn://community/posts/p_1', 'const a = 1;'):
        assert fragment not in masked


def test_mask_keeps_link_text_translatable_but_protects_url() -> None:
    """Markdown 链接只遮 URL，链接文字仍要能翻。"""
    masked, fragments = mask_protected('见[官方文档](https://docs.example.com/zh/guide)了解详情')
    assert '官方文档' in masked, '链接文字必须留在待翻正文里'
    assert 'https://docs.example.com/zh/guide' in fragments


def test_restore_raises_when_placeholder_dropped() -> None:
    """模型吞掉占位符 → 结构已破坏，必须显式失败而不是尽力回填。"""
    _masked, fragments = mask_protected('代码 `foo()` 在这里')
    with pytest.raises(MarkdownStructureError):
        restore_protected('the code is here', fragments)


def test_restore_raises_on_unknown_leftover_placeholder() -> None:
    with pytest.raises(MarkdownStructureError):
        restore_protected('text [[HX-7]] more', [])


# ============================ 长文分段 ============================


def test_split_long_text_keeps_short_text_single_chunk() -> None:
    assert split_long_text('短文', max_chars=3000) == ['短文']


def test_split_long_text_never_breaks_fenced_code() -> None:
    code = '\n\n'.join(f'line_{i} = {i}' for i in range(80))
    text = f'开头。\n\n```python\n{code}\n```\n\n结尾。'
    for chunk in split_long_text(text, max_chars=200):
        assert chunk.count('```') % 2 == 0


def test_split_long_text_is_lossless() -> None:
    paras = [f'第 {i} 段。' + '内容' * 300 for i in range(8)]
    text = '\n\n'.join(paras)
    assert '\n\n'.join(split_long_text(text, max_chars=1500)) == text


# ============================ 术语表 ============================


def test_glossary_terms_lock_product_names() -> None:
    en = glossary_terms('en')
    assert en['唤星'] == 'Astra', '产品名铁律：唤星 → Astra，不是 Huanxing'
    assert en['分身'] == 'Agent'


def test_glossary_prompt_block_only_includes_terms_present_in_text() -> None:
    """只注入正文里真出现过的词条，避免白烧 prompt token。"""
    block = glossary_prompt_block('en', source_text='这条帖子提到了分身，没提别的。')
    assert '分身 -> Agent' in block
    assert '唤星' not in block


def test_glossary_prompt_block_empty_when_no_term_hits() -> None:
    assert glossary_prompt_block('en', source_text='一段完全不含术语的文本。') == ''


def test_glossary_shared_contract_matches_track_a_copy_when_available() -> None:
    """术语表与轨道 A 的 `webui/scripts/i18n/glossary.json` 的**共享契约**必须一致。

    共享契约 = ``terms``（注入两侧翻译 prompt 的指定译法）+ ``forbidden``（产品名禁写法）。
    这两段一漂，就会出现「按钮上叫 Agent、帖子译文里叫别的」，用户以为是两个东西。

    **不比整份文件**：``overrides``/``audits`` 是轨道 A 静态文案管线的工具配置
    （存量译文一次性校正、同中文多英译的人工裁决），只对界面文案有意义，云端内容翻译
    根本不消费；拿它们当门禁只会让两边为了对齐无关配置而互相绊住。

    跨仓文件，只有 hasn-node 也 checkout 在旁边时才比得了；找不到就跳过并说明原因，
    **不假装通过**。
    """
    here = Path(__file__).resolve()
    cloud = here.parents[2] / 'backend/common/translation/glossary.json'
    # 逐级向上找父项目下的 hasn-node（本仓可能是主 clone，也可能是 .worktrees/<分支> 里的工作树，
    # 层级深度不同，所以不写死层数）。
    relative = Path('hasn-node/webui/scripts/i18n/glossary.json')
    webui = next((parent / relative for parent in here.parents if (parent / relative).exists()), None)
    if webui is None:
        pytest.skip('未找到同级 hasn-node 仓的 webui/scripts/i18n/glossary.json，跨仓一致性无法在此校验')

    cloud_data = json.loads(cloud.read_text('utf-8'))
    webui_data = json.loads(webui.read_text('utf-8'))
    for section in ('terms', 'forbidden'):
        assert cloud_data.get(section) == webui_data.get(section), (
            f'云端与 webui 的术语表 `{section}` 段已漂移：'
            '界面里叫 Agent、内容译文里叫别的，用户会以为是两个东西'
        )


# ============================ 翻译器 ============================


def _translator(handler) -> ContentTranslator:
    return ContentTranslator(
        LLMChatClient(
            base_url='http://gw.local',
            api_key='sk-test',
            model='agnes-2.5-flash',
            transport=httpx.MockTransport(handler),
        )
    )


def _ok(content: str, *, total_tokens: int = 0) -> httpx.Response:
    body: dict = {'choices': [{'message': {'content': content}}]}
    if total_tokens:
        body['usage'] = {'total_tokens': total_tokens}
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_translate_markdown_preserves_structure_end_to_end() -> None:
    """模型原样回带占位符的译文 → 回填后代码块/URL/@提及/hasn:// 全都完整。"""
    original = (
        '@王五 请看 #唤星# 的 `demo()`：\n\n'
        '```python\nprint("hi")\n```\n\n'
        '链接 https://example.com/x 和 hasn://community/posts/p_9'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body['messages'][-1]['content']
        masked_body = user.split('\n\n', 1)[1]
        # 模拟一次「只翻散文、原样保留占位符」的正常翻译
        return _ok(masked_body.replace('请看', 'please see').replace('链接', 'link'), total_tokens=123)

    outcome = await _translator(handler).translate_markdown(original, source_lang='zh', target_lang='en')
    assert 'print("hi")' in outcome.text
    assert 'https://example.com/x' in outcome.text
    assert 'hasn://community/posts/p_9' in outcome.text
    assert '@王五' in outcome.text
    assert '#唤星#' in outcome.text
    assert '`demo()`' in outcome.text
    assert 'please see' in outcome.text
    assert outcome.engine == 'agnes-2.5-flash'
    assert outcome.token_usage == 123


@pytest.mark.asyncio
async def test_translate_markdown_injects_glossary_into_system_prompt() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body['messages'][0]['content'])
        return _ok('translated')

    await _translator(handler).translate_markdown('分身帮我写的帖子', source_lang='zh', target_lang='en')
    assert '分身 -> Agent' in seen[0]


@pytest.mark.asyncio
async def test_translate_markdown_fails_explicitly_when_gateway_down() -> None:
    """网关不可用 → 抛 TranslationError，**绝不返回原文伪装成译文**。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={'error': 'gateway down'})

    original = '这是原文，不该被当成译文返回。'
    with pytest.raises(TranslationError):
        await _translator(handler).translate_markdown(original, source_lang='zh', target_lang='en')


@pytest.mark.asyncio
async def test_translate_markdown_fails_when_model_drops_placeholder() -> None:
    """模型把占位符吞了 → 结构已坏，显式失败而不是交付坏正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok('the code is gone here')

    with pytest.raises(TranslationError):
        await _translator(handler).translate_markdown(
            '代码 `foo()` 在这里', source_lang='zh', target_lang='en'
        )


@pytest.mark.asyncio
async def test_translate_markdown_rejects_empty_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调到
        raise AssertionError('空原文不该发起 LLM 调用')

    with pytest.raises(TranslationError):
        await _translator(handler).translate_markdown('   ', source_lang='zh', target_lang='en')


@pytest.mark.asyncio
async def test_translate_markdown_splits_long_text_and_sums_tokens() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body['messages'][-1]['content'])
        return _ok(f'CHUNK{len(calls)}', total_tokens=10)

    paras = [f'第 {i} 段。' + '内容' * 400 for i in range(8)]
    outcome = await _translator(handler).translate_markdown(
        '\n\n'.join(paras), source_lang='zh', target_lang='en', chunk_chars=1500
    )
    assert len(calls) > 1, '长文应分段成多次调用'
    assert outcome.token_usage == 10 * len(calls), 'token 记账应累加各分块'
    assert outcome.text.count('CHUNK') == len(calls)


def test_source_hash_is_stable_and_content_sensitive() -> None:
    assert source_hash('abc') == source_hash('abc')
    assert source_hash('abc') != source_hash('abd')
    assert len(source_hash('abc')) == 64
