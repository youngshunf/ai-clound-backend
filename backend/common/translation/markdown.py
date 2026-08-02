"""Markdown 结构保留：不可翻片段的遮罩/回填 + 长文按段落分段（公共件）。

用户内容（帖子/文章正文）是 Markdown，里面混着**一翻就坏**的东西：代码块、行内代码、
URL、`@某人`、`#话题`、`hasn://` URI。全靠 prompt 叮嘱模型「别翻这些」并不可靠——
模型偶尔会把 URL 里的英文单词也翻了，把 `@张三` 翻成 `@Zhang San`（于是 @ 提及失效）。

所以走「遮罩 → 翻译 → 回填」：翻译前把这些片段替换成占位符，翻完再原样换回来。
占位符选 ``[[HX-0]]`` 这种非自然语言形态，模型基本不动它；**回填时逐个校验占位符
是否都还在**，缺了就判定结构已破坏并显式失败（零 fake：宁可让 UI 显示「翻译失败」，
也不返回一段 @ 提及和链接被翻烂的正文）。
"""

from __future__ import annotations

import re

from typing import Final

# 占位符形态：非自然语言、无空格、不含可翻译词，模型跨语言时基本原样保留。
_PLACEHOLDER_TMPL: Final = '[[HX-{index}]]'
_PLACEHOLDER_RE: Final = re.compile(r'\[\[HX-\d+]]')

# 遮罩规则，**顺序敏感**：先长后短，否则围栏代码会先被行内代码规则咬掉一半。
_PROTECT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # 围栏代码块（``` 或 ~~~，含语言标注），跨行
    re.compile(r'(?ms)^[ \t]*(?P<fence>```|~~~)[^\n]*\n.*?^[ \t]*(?P=fence)[ \t]*$'),
    # 行内代码 `code`
    re.compile(r'`[^`\n]+`'),
    # Markdown 图片/链接的 URL 部分：![alt](url) / [text](url) —— 只遮 url，alt/text 仍可翻
    re.compile(r'(?<=]\()[^)\s]+(?=\))'),
    # 裸 URI：hasn:// 与 http(s)://
    re.compile(r'\bhasn://[^\s<>()\[\]，。；！？、]+'),
    re.compile(r'\bhttps?://[^\s<>()\[\]，。；！？、]+'),
    # @提及：@ 后跟中英数下划线连字符（中文昵称也要覆盖）
    re.compile(r'@[\w一-鿿-]+'),
    # #话题#（中文社区习惯的闭合写法）与 #topic（半角空格结束）
    re.compile(r'#[^#\s]+#'),
    re.compile(r'(?<![\w#])#[\w一-鿿-]+'),
)


class MarkdownStructureError(Exception):
    """译文丢失了遮罩占位符 —— 结构已破坏，调用方必须判失败，不得返回半成品。"""


def mask_protected(text: str) -> tuple[str, list[str]]:
    """把不可翻片段替换成占位符。

    返回 ``(遮罩后的文本, 原片段列表)``；列表下标即占位符编号，回填时按序还原。
    """
    fragments: list[str] = []

    def _take(match: re.Match[str]) -> str:
        # 占位符自身不再二次遮罩（多轮 pattern 会重复扫描同一段文本）
        raw = match.group(0)
        if _PLACEHOLDER_RE.fullmatch(raw):
            return raw
        fragments.append(raw)
        return _PLACEHOLDER_TMPL.format(index=len(fragments) - 1)

    masked = text
    for pattern in _PROTECT_PATTERNS:
        masked = pattern.sub(_take, masked)
    return masked, fragments


def restore_protected(text: str, fragments: list[str]) -> str:
    """把占位符换回原片段；任一占位符缺失即抛 :class:`MarkdownStructureError`。

    缺失意味着模型把占位符翻掉/吞掉了，此时正文里对应位置的代码块或链接已经没了，
    继续回填只会产出一段结构错乱的 Markdown——所以这里显式失败而不是尽力而为。
    """
    restored = text
    for index, fragment in enumerate(fragments):
        placeholder = _PLACEHOLDER_TMPL.format(index=index)
        if placeholder not in restored:
            raise MarkdownStructureError(f'译文丢失占位符 {placeholder}（原片段: {fragment[:40]!r}）')
        restored = restored.replace(placeholder, fragment)
    # 反向校验：不该再有残留占位符（模型自己编了一个的情况）
    leftover = _PLACEHOLDER_RE.search(restored)
    if leftover:
        raise MarkdownStructureError(f'译文残留未知占位符 {leftover.group(0)}')
    return restored


def split_long_text(text: str, max_chars: int = 3000) -> list[str]:
    """按段落边界把长文切成不超过 ``max_chars`` 的块；短文原样单块返回。

    **绝不在未闭合的代码围栏内部切块**——切开后两半各自是非法 Markdown，模型会当成
    普通文本把代码也翻了。判据是当前块内 ``` 的个数为偶数（围栏已闭合）。
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ''
    for paragraph in text.split('\n\n'):
        candidate = f'{current}\n\n{paragraph}' if current else paragraph
        if current and len(candidate) > max_chars and current.count('```') % 2 == 0:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
