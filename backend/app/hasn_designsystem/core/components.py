"""组件清单提取（Python 移植 hasn-designsystem-core `components.rs`）：
``components.html`` + 可选 ``tokens.css`` → ``components.manifest.json``
（style 块统计、选择器/类/元素清单、token 声明/引用/未用/未声明、组分类、反模式字面量）。

Rust ``regex`` crate → Python ``re``（``(?i)``/``(?is)``/``\\b``/非贪婪/replace_all 语义对齐）；
手写字符扫描（class 提取、meta description、像素/字体字面量计数）忠实 Rust byte 级实现。
"""

from __future__ import annotations

import re

from typing import Any

from . import css
from .scenes import detect_scenes

# components.manifest schema 版本（v2：新增 scenes[] 场景覆盖报告，DSGAL）。
COMPONENTS_MANIFEST_SCHEMA_VERSION = 2

# Rust `u8::is_ascii_whitespace`：空格 / \t / \n / \x0c / \r（不含 vertical tab）。
_ASCII_WS = ' \t\n\x0c\r'


def _is_word_byte(ch: str) -> bool:
    """Rust `is_word = alnum | '_'`（用于像素字面量前后边界）。"""
    return ch.isascii() and (ch.isalnum() or ch == '_')


def _is_ascii_digit(ch: str) -> bool:
    return ch.isascii() and ch.isdigit()


# ── 组分类定义（每组 selector/class/element 匹配器；顺序与 Rust `group_defs` 一致）──
class _GroupDef:
    __slots__ = ('class_matchers', 'element_matchers', 'id', 'label', 'selector_matchers')

    def __init__(
        self,
        group_id: str,
        label: str,
        selector_patterns: list[str],
        class_patterns: list[str],
        element_patterns: list[str],
    ) -> None:
        self.id = group_id
        self.label = label
        self.selector_matchers = [re.compile(p) for p in selector_patterns]
        self.class_matchers = [re.compile(p) for p in class_patterns]
        self.element_matchers = [re.compile(p) for p in element_patterns]


_GROUP_DEFS: list[_GroupDef] = [
    _GroupDef(
        'buttons',
        'Buttons and calls to action',
        [r'(?i)\bbutton\b', r'(?i)\.btn(?:\b|[-_:])', r"""(?i)\[type=["']?(?:button|submit|reset)"""],
        [r'(?i)^btn(?:$|-)', r'(?i)button', r'(?i)cta'],
        [r'(?i)^button$'],
    ),
    _GroupDef(
        'inputs',
        'Form fields and controls',
        [
            r'(?i)\binput\b',
            r'(?i)\btextarea\b',
            r'(?i)\bselect\b',
            r'(?i)\.field(?:\b|[-_:])',
            r'(?i)\blabel\b',
        ],
        [r'(?i)^field(?:$|-)', r'(?i)input', r'(?i)control', r'(?i)form'],
        [r'(?i)^(input|textarea|select|label|form)$'],
    ),
    _GroupDef(
        'cards',
        'Cards and panels',
        [r'(?i)\.card(?:\b|[-_:])', r'(?i)\.panel(?:\b|[-_:])', r'(?i)\.tile(?:\b|[-_:])'],
        [r'(?i)^card(?:$|-)', r'(?i)^panel(?:$|-)', r'(?i)^tile(?:$|-)'],
        [],
    ),
    _GroupDef(
        'badges',
        'Badges, chips, and status labels',
        [
            r'(?i)\.badge(?:\b|[-_:])',
            r'(?i)\.chip(?:\b|[-_:])',
            r'(?i)\.tag(?:\b|[-_:])',
            r'(?i)\.pill(?:\b|[-_:])',
        ],
        [
            r'(?i)^badge(?:$|-)',
            r'(?i)^chip(?:$|-)',
            r'(?i)^tag(?:$|-)',
            r'(?i)^pill(?:$|-)',
            r'(?i)status',
        ],
        [],
    ),
    _GroupDef(
        'links',
        'Links and inline actions',
        [r'(?i)\ba\b', r'(?i)\.link(?:\b|[-_:])'],
        [r'(?i)^link(?:$|-)'],
        [r'(?i)^a$'],
    ),
    _GroupDef(
        'keyboard',
        'Keyboard hints',
        [r'(?i)\bkbd\b', r'(?i)\.kbd(?:\b|[-_:])'],
        [r'(?i)^kbd(?:$|-)', r'(?i)keyboard', r'(?i)shortcut'],
        [r'(?i)^kbd$'],
    ),
    _GroupDef(
        'icons',
        'Icon slots',
        [r'(?i)\.icon(?:\b|[-_:])', r"""(?i)\[aria-hidden=["']true["']\]"""],
        [r'(?i)^icon(?:$|-)'],
        [r'(?i)^svg$'],
    ),
    _GroupDef(
        'typography',
        'Typography scale and text utilities',
        [
            r'(?i)\bh[1-6]\b',
            r'(?i)\.lead(?:\b|[-_:])',
            r'(?i)\.eyebrow(?:\b|[-_:])',
            r'(?i)\.body-(?:muted|sm|small)\b',
        ],
        [
            r'(?i)^lead$',
            r'(?i)^eyebrow$',
            r'(?i)^body-(?:muted|sm|small)$',
            r'(?i)caption',
        ],
        [r'(?i)^h[1-6]$', r'(?i)^p$'],
    ),
    _GroupDef(
        'layout',
        'Layout primitives',
        [
            r'(?i)\.container(?:\b|[-_:])',
            r'(?i)\.stack-\d+\b',
            r'(?i)\.row-(?:between|center|start|end)\b',
            r'(?i)\bsection\b',
            r'(?i)\bmain\b',
            r'(?i)\bnav\b',
        ],
        [
            r'(?i)^container$',
            r'(?i)^stack-\d+$',
            r'(?i)^row-(?:between|center|start|end)$',
            r'(?i)grid',
            r'(?i)layout',
        ],
        [r'(?i)^(main|section|nav|header|footer)$'],
    ),
]

_STYLE_BLOCK_RE = re.compile(r'(?is)<style\b[^>]*>(.*?)</style>')
_ELEMENT_RE = re.compile(r'(?i)<\s*([a-z][a-z0-9-]*)')
_TOKEN_NAME_RE = re.compile(r'(--[a-zA-Z0-9_-]+)\s*:')
_SELECTOR_RE = re.compile(r'(?:^|[{}])\s*([^@{}][^{}]*?)\s*\{')
_RULE_RE = re.compile(r'(?:^|[{}])\s*([^@{}][^{}]*?)\s*\{([^{}]*)\}')
_KEYFRAME_SEL_RE = re.compile(r'(?i)^(?:from|to|\d+(?:\.\d+)?%)$')
_TITLE_RE = re.compile(r'(?is)<title\b[^>]*>(.*?)</title>')
_AT_RULE_HEADER_RE = re.compile(r'(?i)@(?:media|supports|container|layer)\b[^{]*\{')
_ROOT_BLOCK_RE = re.compile(r'(?s):root(?:\[[^\]]+\])?\s*\{.*?\}')
_COLOR_LITERAL_RE = re.compile(r'(?i)#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)|color-mix\([^)]*\)')


def _extract_style_blocks(html: str) -> list[str]:
    return [m.group(1).strip() for m in _STYLE_BLOCK_RE.finditer(html)]


def _strip_container_at_rule_headers(source: str) -> str:
    return _AT_RULE_HEADER_RE.sub('{', source)


def _strip_root_blocks(source: str) -> str:
    return _ROOT_BLOCK_RE.sub('', source)


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted(set(values))


def _split_selector_list(selector_list: str) -> list[str]:
    selectors: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in selector_list:
        if ch in '([':
            depth += 1
            current.append(ch)
        elif ch in ')]':
            depth = max(depth - 1, 0)
            current.append(ch)
        elif ch == ',' and depth == 0:
            selectors.append(''.join(current))
            current = []
        else:
            current.append(ch)
    selectors.append(''.join(current))
    return selectors


def _normalize_selector(selector: str) -> str:
    return css.collapse_whitespace(selector.strip())


def _is_keyframe_selector(raw: str) -> bool:
    return _KEYFRAME_SEL_RE.search(raw) is not None


def _extract_css_selectors(source: str) -> list[str]:
    commentless = _strip_container_at_rule_headers(css.strip_css_comments(source))
    selectors: set[str] = set()
    for match in _SELECTOR_RE.finditer(commentless):
        raw = match.group(1).strip()
        if not raw or ':root' in raw or _is_keyframe_selector(raw):
            continue
        for selector in _split_selector_list(raw):
            normalized = _normalize_selector(selector)
            if normalized and not normalized.startswith('@'):
                selectors.add(normalized)
    return sorted(selectors)


def _extract_selector_token_references(source: str) -> dict[str, list[str]]:
    commentless = _strip_container_at_rule_headers(css.strip_css_comments(source))
    by_selector: dict[str, set[str]] = {}
    for match in _RULE_RE.finditer(commentless):
        raw = match.group(1).strip()
        body = match.group(2) or ''
        if not raw or ':root' in raw or _is_keyframe_selector(raw):
            continue
        token_refs = _unique_sorted(css.extract_var_references(body))
        if not token_refs:
            continue
        for selector in _split_selector_list(raw):
            normalized = _normalize_selector(selector)
            if not normalized or normalized.startswith('@'):
                continue
            entry = by_selector.setdefault(normalized, set())
            entry.update(token_refs)
    return {selector: sorted(refs) for selector, refs in by_selector.items()}


def _extract_html_classes(html: str) -> list[str]:
    """手写带引号匹配（正则回引用不便，忠实 Rust byte 扫描）。"""
    classes: set[str] = set()
    lower = html.lower()
    n = len(html)
    search_from = 0
    while True:
        rel = lower.find('class', search_from)
        if rel == -1:
            break
        i = rel + len('class')
        while i < n and html[i] in _ASCII_WS:
            i += 1
        if i >= n or html[i] != '=':
            search_from = rel + len('class')
            continue
        i += 1
        while i < n and html[i] in _ASCII_WS:
            i += 1
        if i >= n or (html[i] != '"' and html[i] != "'"):
            search_from = i
            continue
        quote = html[i]
        i += 1
        value_start = i
        while i < n and html[i] != quote:
            i += 1
        value = html[value_start : min(i, n)]
        for class_name in value.split():
            if class_name:
                classes.add(class_name)
        search_from = min(i + 1, n)
    return sorted(classes)


def _extract_html_elements(html: str) -> list[str]:
    elements: set[str] = set()
    for match in _ELEMENT_RE.finditer(html):
        tag = match.group(1).lower()
        if not tag.startswith('!'):
            elements.add(tag)
    return sorted(elements)


def _parse_token_names(source: str) -> list[str]:
    cleaned = css.strip_css_comments(source)
    names = {match.group(1) for match in _TOKEN_NAME_RE.finditer(cleaned)}
    return sorted(names)


def _decode_basic_entities(value: str) -> str:
    return (
        value
        .replace('&quot;', '"')
        .replace('&#39;', "'")
        .replace('&amp;', '&')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
    )


def _extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if match is None:
        return None
    raw = css.collapse_whitespace(match.group(1).strip())
    if not raw:
        return None
    return _decode_basic_entities(raw)


def _extract_attr(tag: str, attr: str) -> str | None:
    lower = tag.lower()
    n = len(tag)
    search_from = 0
    while True:
        rel = lower.find(attr, search_from)
        if rel == -1:
            return None
        i = rel + len(attr)
        while i < n and tag[i] in _ASCII_WS:
            i += 1
        if i >= n or tag[i] != '=':
            search_from = rel + len(attr)
            continue
        i += 1
        while i < n and tag[i] in _ASCII_WS:
            i += 1
        if i >= n or (tag[i] != '"' and tag[i] != "'"):
            return None
        quote = tag[i]
        i += 1
        value_start = i
        while i < n and tag[i] != quote:
            i += 1
        return tag[value_start : min(i, n)]


def _extract_meta_description(html: str) -> str | None:
    """手写：找 ``<meta ... name=description ... content="...">``。"""
    lower = html.lower()
    n = len(html)
    search_from = 0
    while True:
        rel = lower.find('<meta', search_from)
        if rel == -1:
            return None
        tag_start = rel
        gt = html.find('>', tag_start)
        tag_end = n if gt == -1 else gt
        tag = html[tag_start : min(tag_end, n)]
        tag_lower = lower[tag_start : min(tag_end, len(lower))]
        if 'name="description"' in tag_lower or "name='description'" in tag_lower or 'name=description' in tag_lower:
            content = _extract_attr(tag, 'content')
            if content is not None:
                value = css.collapse_whitespace(content.strip())
                if value:
                    return _decode_basic_entities(value)
        search_from = max(tag_end, tag_start + 1)


def _count_color_expressions(source: str) -> int:
    return sum(1 for _ in _COLOR_LITERAL_RE.finditer(source))


def _count_pixel_values(source: str) -> int:
    """忠实 ``(?<![\\w-])-?\\d*\\.?\\d+px\\b``：前不接 [\\w-]，数字后接 px 且后为词边界。"""
    n = len(source)
    count = 0
    i = 0
    while i < n:
        start = i
        prev_ok = start == 0 or not (_is_word_byte(source[start - 1]) or source[start - 1] == '-')
        j = i
        if j < n and source[j] == '-':
            j += 1
        while j < n and _is_ascii_digit(source[j]):
            j += 1
        if j < n and source[j] == '.':
            j += 1
        digit_start = j
        while j < n and _is_ascii_digit(source[j]):
            j += 1
        had_trailing_digits = j > digit_start
        if prev_ok and had_trailing_digits and source[j:].startswith('px'):
            after = j + 2
            boundary = after >= n or not _is_word_byte(source[after])
            if boundary:
                count += 1
                i = after
                continue
        i += 1
    return count


def _count_hardcoded_font_families(source: str) -> int:
    """忠实 ``\\bfont-family\\s*:\\s*(?!var\\()``：font-family: 后非 var(（Rust 边界含 '-'）。"""
    lower = source.lower()
    n = len(source)

    def is_word(ch: str) -> bool:
        return ch.isascii() and (ch.isalnum() or ch == '_' or ch == '-')

    count = 0
    search_from = 0
    while True:
        rel = lower.find('font-family', search_from)
        if rel == -1:
            break
        start = rel
        before_ok = start == 0 or not is_word(source[start - 1])
        i = start + len('font-family')
        while i < n and source[i] in _ASCII_WS:
            i += 1
        if before_ok and i < n and source[i] == ':':
            i += 1
            while i < n and source[i] in _ASCII_WS:
                i += 1
            if not lower[min(i, len(lower)) :].startswith('var('):
                count += 1
        search_from = start + len('font-family')
    return count


def _build_group(
    definition: _GroupDef,
    selectors: list[str],
    selector_token_refs: dict[str, list[str]],
    classes: list[str],
    elements: list[str],
    referenced_tokens: list[str],
) -> dict[str, Any]:
    matched_selectors = [s for s in selectors if any(m.search(s) for m in definition.selector_matchers)]
    matched_classes = [c for c in classes if any(m.search(c) for m in definition.class_matchers)]
    matched_elements = [e for e in elements if any(m.search(e) for m in definition.element_matchers)]
    referenced_set = set(referenced_tokens)
    all_refs: list[str] = []
    for selector in matched_selectors:
        all_refs.extend(selector_token_refs.get(selector, []))
    token_references = [t for t in _unique_sorted(all_refs) if t in referenced_set]

    return {
        'id': definition.id,
        'label': definition.label,
        'present': bool(matched_selectors or matched_classes or matched_elements),
        'selectors': matched_selectors,
        'classes': matched_classes,
        'elements': matched_elements,
        'tokenReferences': token_references,
    }


def extract_components(
    brand_id: str,
    fixture_html: str,
    tokens_css: str | None = None,
) -> dict[str, Any]:
    """提取组件清单（移植 `extractComponentsManifest`）。返回 camelCase manifest dict。"""
    style_blocks = _extract_style_blocks(fixture_html)
    style_css = '\n\n'.join(style_blocks)
    selectors = _extract_css_selectors(style_css)
    selector_token_refs = _extract_selector_token_references(style_css)
    classes = _extract_html_classes(fixture_html)
    elements = _extract_html_elements(fixture_html)

    declared_source = tokens_css if tokens_css is not None else css.extract_first_root_body(style_css) or ''
    declared_tokens = _parse_token_names(declared_source)
    referenced_tokens = _unique_sorted(css.extract_var_references(fixture_html))

    declared_set = set(declared_tokens)
    referenced_set = set(referenced_tokens)
    unused_declared = [t for t in declared_tokens if t not in referenced_set]
    undeclared_referenced = [] if not declared_tokens else [t for t in referenced_tokens if t not in declared_set]

    groups = [
        _build_group(
            definition,
            selectors,
            selector_token_refs,
            classes,
            elements,
            referenced_tokens,
        )
        for definition in _GROUP_DEFS
    ]

    literals_css = _strip_root_blocks(css.strip_css_comments(style_css))

    source: dict[str, Any] = {'componentsHtml': 'components.html'}
    if tokens_css is not None:
        source['tokensCss'] = 'tokens.css'

    fixture: dict[str, Any] = {}
    title = _extract_title(fixture_html)
    if title is not None:
        fixture['title'] = title
    description = _extract_meta_description(fixture_html)
    if description is not None:
        fixture['description'] = description
    fixture['styleBlockCount'] = len(style_blocks)
    fixture['selectorCount'] = len(selectors)
    fixture['classCount'] = len(classes)
    fixture['elementCount'] = len(elements)

    return {
        'schemaVersion': COMPONENTS_MANIFEST_SCHEMA_VERSION,
        'brandId': brand_id,
        'source': source,
        'fixture': fixture,
        'tokens': {
            'declared': declared_tokens,
            'referenced': referenced_tokens,
            'unusedDeclared': unused_declared,
            'undeclaredReferenced': undeclared_referenced,
        },
        'selectors': selectors,
        'classes': classes,
        'elements': elements,
        'groups': groups,
        # DSGAL：场景覆盖报告（品牌网站/演示文稿/海报/移动端标准组件到位情况），纯函数只看 HTML 标记。
        'scenes': detect_scenes(fixture_html),
        'literals': {
            'colorExpressions': _count_color_expressions(literals_css),
            'pixelValues': _count_pixel_values(literals_css),
            'hardcodedFontFamilies': _count_hardcoded_font_families(literals_css),
        },
    }
