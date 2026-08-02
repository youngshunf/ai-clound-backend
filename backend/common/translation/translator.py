"""内容翻译核心：遮罩 → 分段 → 调 LLM → 回填校验（公共件）。

与 marketplace 的技能元数据翻译不同，这里翻的是**用户写的正文**，因此三条约束更硬：

1. **结构必须原样**（代码块/链接/@提及/`hasn://` URI）—— 见 :mod:`.markdown`。
2. **术语必须一致**（分身=Agent，唤星=Astra）—— 见 :mod:`.glossary`。
3. **失败就是失败**，绝不返回原文冒充译文 —— 上层据此给 UI 明确的错误态。

第 3 条是与 `marketplace/service/translation_service.py::translate` 的关键分野：那个方法
在异常时 `return text`（回落原文），对「技能描述双语化」这种锦上添花的场景可以接受，但
用户点了「翻译」却拿到一模一样的中文，是纯粹的欺骗。本模块一律抛
:class:`TranslationError`。
"""

from __future__ import annotations

import asyncio
import hashlib

from dataclasses import dataclass, field
from typing import Any

from backend.common.llm import LLMChatClient, LLMError
from backend.common.log import log
from backend.common.translation.glossary import glossary_prompt_block
from backend.common.translation.language import language_name, normalize_language
from backend.common.translation.markdown import (
    MarkdownStructureError,
    mask_protected,
    restore_protected,
    split_long_text,
)

# 单块送 LLM 的字符预算。超过就按段落切；文章常见长文必须支持。
DEFAULT_CHUNK_CHARS = 3000
# 分段并发上限：太高会把网关打限流，太低长文等太久。
_CHUNK_CONCURRENCY = 4


class TranslationError(Exception):
    """翻译失败（网关穷尽、结构破坏、空译文）。**调用方必须把它表达成错误，不得回落原文。**"""


@dataclass(slots=True)
class TranslationOutcome:
    """一次翻译的结果与记账信息。"""

    text: str
    engine: str
    token_usage: int = 0
    #: 各分块的 usage 原始 dict，便于排障时看清是哪一块烧的 token
    usage_details: list[dict[str, Any]] = field(default_factory=list)


def source_hash(text: str) -> str:
    """原文 sha256（十六进制小写）—— 译文缓存键的一部分，原文一改即自动失效。"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _total_tokens(usage: dict[str, Any] | None) -> int:
    """从网关 usage 取 total_tokens；缺字段回 0（表示未知，不估算）。"""
    if not isinstance(usage, dict):
        return 0
    value = usage.get('total_tokens')
    return value if isinstance(value, int) and value >= 0 else 0


class ContentTranslator:
    """用户内容翻译器（Markdown 结构保留 + 术语表 + 长文分段）。"""

    def __init__(self, llm: LLMChatClient) -> None:
        self._llm = llm

    async def translate_markdown(
        self,
        text: str,
        *,
        source_lang: str,
        target_lang: str,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
    ) -> TranslationOutcome:
        """翻译一段 Markdown 正文，保留全部结构；失败抛 :class:`TranslationError`。"""
        if not text or not text.strip():
            raise TranslationError('原文为空，无可翻内容')

        source = normalize_language(source_lang)
        target = normalize_language(target_lang)

        chunks = split_long_text(text, max_chars=chunk_chars)
        semaphore = asyncio.Semaphore(_CHUNK_CONCURRENCY)

        async def run(chunk: str) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                return await self._translate_chunk(chunk, source=source, target=target)

        # 分段并发翻，顺序由 gather 保证（长文按段落切，拼回时必须保持原顺序）。
        results = await asyncio.gather(*(run(chunk) for chunk in chunks))

        translated = '\n\n'.join(part for part, _ in results).strip()
        if not translated:
            raise TranslationError('LLM 返回空译文')

        usages = [usage for _, usage in results]
        return TranslationOutcome(
            text=translated,
            engine=self._engine_name(),
            token_usage=sum(_total_tokens(usage) for usage in usages),
            usage_details=usages,
        )

    async def _translate_chunk(
        self, chunk: str, *, source: str, target: str
    ) -> tuple[str, dict[str, Any]]:
        """翻一块：遮罩 → LLM → 回填校验。"""
        masked, fragments = mask_protected(chunk)

        try:
            raw, usage = await self._llm.complete_with_usage(
                self._messages(masked, source=source, target=target, has_placeholders=bool(fragments)),
                # 译文可能比原文长（欧语系比中文长 40-80%），预算给足并留出结构开销。
                max_tokens=min(8000, len(masked) * 2 + 800),
                temperature=0,
                timeout=min(300.0, 60.0 + len(masked) / 200.0),
            )
        except LLMError as exc:
            # 网关穷尽模型链仍失败：可重试的外部依赖故障，按日志规范记 warn，由上层转成明确错误态。
            log.warning(f'内容翻译调用 LLM 失败（{source}->{target}, {len(chunk)} 字）: {exc}')
            raise TranslationError(f'翻译服务暂不可用: {exc}') from exc

        translated = (raw or '').strip()
        if not translated:
            raise TranslationError('LLM 返回空译文')

        try:
            restored = restore_protected(translated, fragments)
        except MarkdownStructureError as exc:
            # 模型把占位符翻掉了 → 代码块/链接已丢，返回它等于交付一段坏正文。
            log.warning(f'内容翻译结构校验失败（{source}->{target}）: {exc}')
            raise TranslationError(f'译文结构校验失败: {exc}') from exc

        return restored, usage

    def _messages(
        self, masked: str, *, source: str, target: str, has_placeholders: bool
    ) -> list[dict[str, str]]:
        """构造 chat messages：系统约束（结构/术语/占位符）+ 待翻正文。"""
        source_name = language_name(source) if source else 'the source language'
        target_name = language_name(target)

        rules = [
            'You are a professional translator for social-media content written in Markdown.',
            'Translate the prose accurately and naturally.',
            'Preserve ALL Markdown structure exactly: headings, lists, tables, blockquotes, '
            'link/image syntax, and inline emphasis.',
            'Prefer a faithful translation over a creative one.',
            'Return ONLY the translated Markdown. No commentary, no surrounding code fences.',
        ]
        if has_placeholders:
            rules.append(
                'The text contains placeholders shaped like [[HX-0]], [[HX-1]]. They stand for code, '
                'URLs, @mentions and #topics. Copy every placeholder into the output VERBATIM, keeping '
                'the same count and the same numbers. Never translate, renumber, drop or reword them.'
            )

        system = '\n'.join(rules)
        terminology = glossary_prompt_block(target, source_text=masked)
        if terminology:
            system = f'{system}\n\n{terminology}'

        return [
            {'role': 'system', 'content': system},
            {
                'role': 'user',
                'content': f'Translate the following Markdown from {source_name} to {target_name}.\n\n{masked}',
            },
        ]

    def _engine_name(self) -> str:
        """本次翻译实际使用的引擎名（落缓存表 ``engine`` 列）。"""
        chain = self._llm._default_model_chain()
        return chain[0] if chain else 'unknown'
