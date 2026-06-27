"""deck 页 HTML 片段骨架校验（忠实移植 hasn-node daemon 的 Rust 校验器）。

云端分身经 `hasn.deck.page.write` / `page.write_batch` 写页时，必须与本地分身（daemon
`apps/daemon/src/domains/deck/mcp_gateway.rs::validate_page_skeleton`）**同口径**校验，
否则同一 deck 在 web（本地 daemon）与云端分身两路写出的页会出现「一端通过一端被拒」的漂移。

⚠️ **事实源 = hasn-node `mcp_gateway.rs::validate_page_skeleton` + 其私有 helper（移植自
oh-my-ppt 骨架硬校验）**。两端规则与**错误文案逐字对齐**（TOOLMIG2-P3，福仔选 B：完整迁 deck）。
任一端改动校验规则/文案，必须同步另一端。返回 None=合格；返回中文原因串（多条以 `；` 连接）=不合格。
"""

from __future__ import annotations

# 需严格配平的标签（任一不平衡即视为截断/漏闭合）——移植自 oh-my-ppt STRICT_TAGS。
_STRICT_BALANCED_TAGS = (
    'div', 'section', 'main', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr',
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'article', 'header', 'footer',
    'aside', 'figure', 'figcaption', 'blockquote',
)


def _is_word_char(c: str) -> bool:
    """JS 标识符字符（字母/数字/`_`/`$`），ASCII 口径。"""
    return c.isascii() and (c.isalnum() or c in '_$')


def _is_name_boundary_char(c: str | None) -> bool:
    """名字边界：非（字母数字 / `-`）即边界（None 视为边界）。"""
    return c is None or not (c.isascii() and (c.isalnum() or c == '-'))


def _contains_element_tag(html: str) -> bool:
    """是否含至少一个开标签（`<` 紧跟 ASCII 字母）。"""
    return any(html[i] == '<' and html[i + 1].isascii() and html[i + 1].isalpha() for i in range(len(html) - 1))


def _count_open_tags(lower: str, name: str) -> int:
    """统计 `<name` 开标签出现次数（带名字边界，排除 `<p` 命中 `<path`）。"""
    needle = f'<{name}'
    count = 0
    start = 0
    while True:
        pos = lower.find(needle, start)
        if pos < 0:
            break
        after = pos + len(needle)
        nxt = lower[after] if after < len(lower) else None
        if _is_name_boundary_char(nxt):
            count += 1
        start = after
    return count


def _has_open_tag(lower: str, name: str) -> bool:
    return _count_open_tags(lower, name) > 0


def _has_script_with_src(lower: str) -> bool:
    """存在带 `src` 属性的 `<script>` 开标签（inline 无 src 的 `<script>` 放行）。"""
    start = 0
    n = len(lower)
    while True:
        pos = lower.find('<script', start)
        if pos < 0:
            break
        end_rel = lower.find('>', pos)
        end = end_rel if end_rel >= 0 else n
        open_tag = lower[pos:end]
        for tok in open_tag.split():
            if tok == 'src' or tok.startswith('src='):
                return True
        start = min(end + 1, n)
        if start <= pos:
            break
    return False


def _has_remote_css_url(lower: str) -> bool:
    """远程 CSS URL：`url(` 后（忽略空白/引号、可选 `http(s):`）紧跟 `//`。"""
    start = 0
    n = len(lower)
    while True:
        pos = lower.find('url(', start)
        if pos < 0:
            break
        i = pos + 4
        while i < n and lower[i] in ' \t\n\r"\'':
            i += 1
        if lower[i:].startswith('https:'):
            i += 6
        elif lower[i:].startswith('http:'):
            i += 5
        if lower[i:].startswith('//'):
            return True
        start = pos + 4
    return False


def _call_positions(lower: str, name: str) -> list[int]:
    """`name` 作为独立标识符、且后接（可空白）`(` 的所有出现位置（name 起始偏移）。"""
    out: list[int] = []
    start = 0
    n = len(lower)
    while True:
        pos = lower.find(name, start)
        if pos < 0:
            break
        end = pos + len(name)
        prev_is_word = pos > 0 and _is_word_char(lower[pos - 1])
        next_is_word = end < n and _is_word_char(lower[end])
        if not prev_is_word and not next_is_word:
            k = end
            while k < n and lower[k] in ' \t\n\r':
                k += 1
            if k < n and lower[k] == '(':
                out.append(pos)
        start = end
    return out


def _has_new_chart(lower: str) -> bool:
    """直接 `new Chart(`：`chart(` 标识符调用，且前一个词是独立的 `new`。"""
    for abs_pos in _call_positions(lower, 'chart'):
        before = lower[:abs_pos].rstrip()
        if before.endswith('new'):
            head = len(before) - 3
            if head == 0 or not _is_word_char(before[head - 1]):
                return True
    return False


def _has_anime_member(lower: str) -> bool:
    """`anime.` 成员访问（前一个字符非标识符字符）。"""
    start = 0
    while True:
        pos = lower.find('anime.', start)
        if pos < 0:
            break
        if pos == 0 or not _is_word_char(lower[pos - 1]):
            return True
        start = pos + len('anime.')
    return False


def _has_ppt_animate_object_arg(lower: str) -> bool:
    """`PPT.animate({ targets, ... })` 对象签名旧写法。"""
    marker = 'ppt.animate('
    start = 0
    n = len(lower)
    while True:
        pos = lower.find(marker, start)
        if pos < 0:
            break
        abs_pos = pos + len(marker)
        window = lower[abs_pos:min(n, abs_pos + 240)]
        if window.lstrip().startswith('{') and 'targets' in window:
            return True
        start = abs_pos
    return False


def _has_unqualified_anim_call(lower: str) -> bool:
    """未命名空间的 `animate/stagger/createtimeline(` 调用（前一个非空白字符不是 `.`）。"""
    for name in ('animate', 'stagger', 'createtimeline'):
        for abs_pos in _call_positions(lower, name):
            if not lower[:abs_pos].rstrip().endswith('.'):
                return True
    return False


def _has_class_token(lower: str, token: str) -> bool:
    """`token` 作为独立 class token 出现（前后均为名字边界）。"""
    start = 0
    n = len(lower)
    while True:
        pos = lower.find(token, start)
        if pos < 0:
            break
        after = pos + len(token)
        prev_ok = pos == 0 or _is_name_boundary_char(lower[pos - 1])
        next_ok = _is_name_boundary_char(lower[after] if after < n else None)
        if prev_ok and next_ok:
            return True
        start = after
    return False


def _has_hidden_initial_state(lower: str, compact: str) -> bool:
    """默认隐藏态：opacity-0 / invisible class，或 visibility:hidden / display:none / opacity:0（非 0.x）。"""
    if _has_class_token(lower, 'opacity-0') or _has_class_token(lower, 'invisible'):
        return True
    if 'visibility:hidden' in compact or 'display:none' in compact:
        return True
    start = 0
    while True:
        pos = compact.find('opacity:0', start)
        if pos < 0:
            break
        after = pos + len('opacity:0')
        nxt = compact[after] if after < len(compact) else None
        # `opacity:0` 但排除 `opacity:0.5` / `opacity:09` 这类非全透明值。
        if not (nxt is not None and (nxt == '.' or (nxt.isascii() and nxt.isdigit()))):
            return True
        start = after
    return False


def _remove_span(text: str, open_: str, close: str) -> str:
    """剥离 `open_...close` 区间（注释/script/style 内的伪标签不参与配平）。"""
    out: list[str] = []
    rest = text
    while True:
        s = rest.find(open_)
        if s < 0:
            out.append(rest)
            break
        out.append(rest[:s])
        after_open = rest[s + len(open_):]
        e = after_open.find(close)
        if e < 0:
            break
        rest = after_open[e + len(close):]
    return ''.join(out)


def validate_page_skeleton(html: str) -> str | None:  # noqa: C901 — 忠实移植 daemon 九段顺序检查，刻意保持线性可比对
    """校验单页 HTML 片段；合格返回 None，不合格返回中文原因（多条以 `；` 连接）。

    与 hasn-node daemon `validate_page_skeleton` 九段检查逐条对齐。
    """
    trimmed = html.strip()
    if not trimmed:
        return '页 HTML 为空'

    errors: list[str] = []
    lower = html.lower()
    compact = ''.join(c for c in lower if not c.isspace())

    if not _contains_element_tag(trimmed):
        errors.append('页 HTML 不含任何元素标签（疑似纯文本或残缺）')

    # ① 片段模式：禁止完整文档 / head 元信息标签（引擎会补骨架）。
    if _has_open_tag(lower, '!doctype'):
        errors.append('检测到 <!doctype>，请仅传页面片段，不要传完整文档')
    if _has_open_tag(lower, 'html') or '</html>' in lower:
        errors.append('检测到 <html> 标签，请仅传页面片段')
    if _has_open_tag(lower, 'head') or '</head>' in lower:
        errors.append('检测到 <head> 标签，请仅传页面片段')
    if _has_open_tag(lower, 'body') or '</body>' in lower:
        errors.append('检测到 <body> 标签，请仅传页面片段')
    if _has_open_tag(lower, 'meta'):
        errors.append('检测到 <meta> 标签，片段中禁止 head 元信息')
    if _has_open_tag(lower, 'title') or '</title>' in lower:
        errors.append('检测到 <title> 标签，片段中禁止标题标签')
    if _has_open_tag(lower, 'link'):
        errors.append('检测到 <link> 标签，片段禁止引外部字体/资源（字体由引擎注入）')
    if _has_open_tag(lower, 'iframe'):
        errors.append('检测到 <iframe> 标签，页面内不允许嵌套 iframe')

    # ② 外链字体 / 远程 CSS 资源（运行时与字体已本地注入，禁 CDN）。
    if '@font-face' in lower:
        errors.append('检测到 @font-face，片段禁止声明字体（字体由引擎注入）')
    if _has_remote_css_url(lower):
        errors.append('检测到远程 CSS URL，片段禁止引远程字体/样式（禁 CDN）')

    # ③ 引擎骨架标识：分身只传主体片段，绝不传骨架根/容器节点。
    if 'data-ppt-guard-root' in lower:
        errors.append('检测到 data-ppt-guard-root，禁止传入骨架根节点')
    if 'data-page-scaffold' in lower:
        errors.append('检测到 data-page-scaffold，禁止传入骨架容器标识')
    if 'ppt-page-root' in lower or 'ppt-page-content' in lower or 'ppt-page-fit-scope' in lower:
        errors.append('检测到页面骨架类（ppt-page-root/content/fit-scope），请仅传主体片段')

    # ④ 已注入运行时：禁止任何 `<script src>`（inline <script> 放行）。
    if _has_script_with_src(lower):
        errors.append('检测到 <script src>，片段禁止引入脚本资源（运行时已预注入）')

    # ⑤ 图表：必须 `PPT.createChart`，禁直接 `new Chart(`。
    if _has_new_chart(lower):
        errors.append('检测到 new Chart(...)，请改为 PPT.createChart(canvasOrSelector, config)')

    # ⑥ 动画：必须 `PPT.animate(targets, params)`。
    if _call_positions(lower, 'anime'):
        errors.append('检测到 anime(...) 旧写法，请改为 PPT.animate(targets, params)')
    if _has_anime_member(lower):
        errors.append('检测到 anime.* 调用，请改为 PPT.animate/PPT.stagger/PPT.createTimeline')
    if _has_ppt_animate_object_arg(lower):
        errors.append('检测到 PPT.animate({ targets, ... })，请改为 PPT.animate(targets, params)')
    if _has_unqualified_anim_call(lower):
        errors.append('检测到未命名空间动画调用（animate/stagger/createTimeline），请统一改为 PPT.*')

    # ⑦ 初始态必须可见：禁默认隐藏。
    if _has_hidden_initial_state(lower, compact):
        errors.append(
            '检测到默认隐藏态（opacity-0/invisible/visibility:hidden/opacity:0/display:none），'
            '初始态必须可见——动画从 PPT.animate 参数里给（如 opacity:[0,1]）'
        )

    # ⑧ 截断：末尾存在未闭合 `<`。
    trimmed_lower = lower.rstrip()
    lt = trimmed_lower.rfind('<')
    if lt >= 0 and '>' not in trimmed_lower[lt:]:
        errors.append('HTML 末尾存在未闭合标签，内容可能被截断')

    # ⑨ 标签配平（先剥离注释/script/style 内的伪标签）：开闭数量必须一致。
    structural = _remove_span(
        _remove_span(_remove_span(lower, '<!--', '-->'), '<script', '</script>'),
        '<style',
        '</style>',
    )
    for tag in _STRICT_BALANCED_TAGS:
        opens = _count_open_tags(structural, tag)
        closes = structural.count(f'</{tag}>')
        if opens != closes:
            errors.append(
                f'<{tag}> 开闭标签数量不一致（{opens} 开 / {closes} 闭），内容可能被截断或漏闭合'
            )

    if not errors:
        return None
    return '；'.join(errors)
