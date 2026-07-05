"""CSS 解析原语（Python 移植 hasn-designsystem-core `css.rs`）。

忠实移植手写字符级扫描语义（**刻意不用正则**，与 Rust 端逐字符对齐，避免 lookaround
与跨语言正则引擎差异）：``:root(?!\\[)`` 首块提取、声明拆分、``var()`` 引用提取、值类型校验器。
"""

from __future__ import annotations

import string

# Rust `u8::is_ascii_whitespace` 的字符集：空格 / \t / \n / \x0c(form feed) / \r
# （**不含** vertical tab \x0b）。用于逐字符对齐 Rust 的 ASCII 空白跳过。
_ASCII_WS = ' \t\n\x0c\r'


def strip_css_comments(css: str) -> str:
    """剥除 ``/* ... */`` 注释（非贪婪、可跨行）。"""
    out: list[str] = []
    i = 0
    n = len(css)
    while i < n:
        if i + 1 < n and css[i] == '/' and css[i + 1] == '*':
            end = css.find('*/', i + 2)
            if end != -1:
                i = end + 2
                continue
            break
        out.append(css[i])
        i += 1
    return ''.join(out)


def extract_first_root_body(css: str) -> str | None:
    """提取首个 ``:root { ... }`` 块的内部。

    忠实 ``:root(?!\\[)\\s*\\{([\\s\\S]*?)\\}``：``:root`` 后不得紧跟 ``[``，其后仅空白再 ``{``，
    捕获到**首个** ``}``。
    """
    cleaned = strip_css_comments(css)
    search_from = 0
    while True:
        rel = cleaned.find(':root', search_from)
        if rel == -1:
            return None
        after = rel + len(':root')
        # (?!\[)：紧跟字符不得是 '['
        if cleaned[after:].startswith('['):
            search_from = after
            continue
        # \s* 然后 '{'
        j = after
        n = len(cleaned)
        while j < n and cleaned[j] in _ASCII_WS:
            j += 1
        if j < n and cleaned[j] == '{':
            close_rel = cleaned.find('}', j + 1)
            if close_rel != -1:
                return cleaned[j + 1 : close_rel]
        search_from = after


def parse_token_declarations(css: str) -> list[tuple[str, str]]:
    """解析 ``:root`` 内的 token 声明，保序（同名后值覆盖，等价 JS ``Map.set``）。"""
    root_body = extract_first_root_body(css)
    if root_body is None:
        return []
    out: list[list[str]] = []
    index: dict[str, int] = {}
    for raw in root_body.split(';'):
        decl = raw.strip()
        if not decl.startswith('--'):
            continue
        colon = decl.find(':')
        if colon == -1:
            continue
        name = decl[:colon].strip()
        value = collapse_whitespace(decl[colon + 1 :].strip())
        if name in index:
            out[index[name]][1] = value
        else:
            index[name] = len(out)
            out.append([name, value])
    return [(name, value) for name, value in out]


def collapse_whitespace(value: str) -> str:
    """折叠连续空白为单个空格（等价 ``replace(/\\s+/g, ' ')``）。"""
    out: list[str] = []
    in_ws = False
    for ch in value:
        if ch.isspace():
            if not in_ws:
                out.append(' ')
                in_ws = True
        else:
            out.append(ch)
            in_ws = False
    return ''.join(out)


def _is_ascii_alnum(ch: str) -> bool:
    return ('0' <= ch <= '9') or ('a' <= ch <= 'z') or ('A' <= ch <= 'Z')


def extract_var_references(value: str) -> list[str]:
    """提取所有 ``var(--name)`` 引用（忠实 ``var\\(\\s*(--[A-Za-z0-9_-]+)``）。"""
    out: list[str] = []
    n = len(value)
    i = 0
    while True:
        rel = value.find('var(', i)
        if rel == -1:
            break
        j = rel + 4
        # Rust 用 is_ascii_whitespace；限定 ASCII 空白集，避免 unicode 空白误跳。
        while j < n and value[j] in _ASCII_WS:
            j += 1
        if value[j:].startswith('--'):
            start = j
            j += 2
            while j < n and (_is_ascii_alnum(value[j]) or value[j] == '_' or value[j] == '-'):
                j += 1
            out.append(value[start:j])
        i = rel + 4
    return out


def count_substring(haystack: str, needle: str) -> int:
    """数 ``needle`` 在 ``haystack`` 中不重叠出现次数（评审反模式：var(--accent) 可见使用应 <=2）。"""
    if not needle:
        return 0
    count = 0
    i = 0
    while True:
        rel = haystack.find(needle, i)
        if rel == -1:
            break
        count += 1
        i = rel + len(needle)
    return count


# ─── 值类型校验器（忠实 open-design 的正则语义，手写实现）───────────────


def _leading_number_len(s: str) -> int:
    """开头连续数字长度，忠实正则 ``\\d+(\\.\\d+)?``（至少一位整数，故 ``.5`` 不被接受）。"""
    n = len(s)
    i = 0
    while i < n and s[i].isascii() and s[i].isdigit():
        i += 1
    if i > 0 and i < n and s[i] == '.':
        j = i + 1
        while j < n and s[j].isascii() and s[j].isdigit():
            j += 1
        if j > i + 1:
            i = j
    return i


def _is_hex_color(value: str) -> bool:
    if not value.startswith('#'):
        return False
    rest = value[1:]
    hex_len = 0
    for ch in rest:
        if ch in string.hexdigits:
            hex_len += 1
        else:
            break
    return 3 <= hex_len <= 8


def is_color_value(value: str) -> bool:
    """``^(#[0-9a-f]{3,8}|rgb[a]?\\(|hsl[a]?\\(|oklch\\(|color-mix\\(|var\\()`` (i)。"""
    trimmed = value.strip()
    lower = trimmed.lower()
    return _is_hex_color(trimmed) or lower.startswith((
        'rgb(',
        'rgba(',
        'hsl(',
        'hsla(',
        'oklch(',
        'color-mix(',
        'var(',
    ))


def is_font_value(value: str) -> bool:
    """``/[A-Za-z]/.test(v) && v.length<=180``。"""
    return len(value) <= 180 and any('a' <= c <= 'z' or 'A' <= c <= 'Z' for c in value)


def is_length_like(value: str) -> bool:
    """``^(\\d+(\\.\\d+)?(px|rem|em|ch|vw|vh|%)|clamp\\(|calc\\(|var\\()`` (i)。"""
    trimmed = value.strip()
    lower = trimmed.lower()
    if lower.startswith(('clamp(', 'calc(', 'var(')):
        return True
    num = _leading_number_len(trimmed)
    if num == 0:
        return False
    rest = lower[num:]
    return any(rest.startswith(unit) for unit in ('px', 'rem', 'em', 'ch', 'vw', 'vh', '%'))


def is_duration_value(value: str) -> bool:
    """``^(\\d+(\\.\\d+)?m?s|var\\()`` (i)。"""
    trimmed = value.strip()
    lower = trimmed.lower()
    if lower.startswith('var('):
        return True
    num = _leading_number_len(trimmed)
    if num == 0:
        return False
    rest = lower[num:]
    return rest.startswith(('ms', 's'))


def is_easing_value(value: str) -> bool:
    """``^(cubic-bezier\\(|linear|ease(-in|-out|-in-out)?|var\\()`` (i)。"""
    lower = value.strip().lower()
    return lower.startswith(('cubic-bezier(', 'linear', 'ease', 'var('))


def is_shadow_value(value: str) -> bool:
    """``trimmed === 'none' || /^(\\d|0\\s|var\\(|color-mix\\()/i || trimmed.includes(' ')``。"""
    trimmed = value.strip()
    if trimmed == 'none':
        return True
    lower = trimmed.lower()
    if lower.startswith(('var(', 'color-mix(')):
        return True
    if trimmed and trimmed[0].isascii() and trimmed[0].isdigit():
        return True
    return ' ' in trimmed
