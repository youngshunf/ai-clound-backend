"""DS-P3 设计系统导入三入口（shadcn / github / screenshot-url）。

产出**初始 tokens.css 草稿**交分身 `compile_tokens` 标准化（草稿 ≠ 最终）。借鉴 open-design
`design-system-shadcn-import.ts` / `design-system-github-import.ts` 导入器思路，语义对齐：
- **shadcn**：拉 registry item → `cssVars`（theme/light/dark）≈1:1 渲染成 tokens.css（颜色三元组包回 `hsl(...)` 保真）。
- **github**：拉前端仓候选 CSS → 扫 `--自定义属性` → 收敛成 tokens.css 草稿。
- **screenshot/url**：抓页（HTML+内联/链接 CSS）→ 扫主色/字体 → 最小草稿（尽力而为）。

零 fake 原则：抓页失败 / 扫不到主色 **如实报错**，绝不造假兜底。
SSRF 闸：仅 http/https；http 仅 loopback；拒私有/链路本地/保留地址；禁重定向；大小/时长上限。
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket

from collections import Counter
from dataclasses import dataclass, field
from itertools import starmap
from urllib.parse import urljoin, urlparse

import httpx

from backend.common.exception import errors
from backend.common.log import log

# ── 网络安全与体量上限（移植 open-design 的 egress / 预算约束）────────────────
_ALLOWED_SCHEMES = {'http', 'https'}
_FETCH_TIMEOUT = 15.0
_MAX_DOC_BYTES = 2 * 1024 * 1024  # 单文档（registry item / HTML / CSS）上限
_MAX_FILE_BYTES = 512 * 1024  # github 单 CSS 文件上限
_MAX_GITHUB_FILES = 6  # 最多探测的候选 CSS 文件数
_MAX_LINKED_CSS = 4  # screenshot 入口最多抓取的外链 CSS 数
_USER_AGENT = 'huanxing-designsystem-import/1.0'

_DRAFT_NOTE = '草稿仅供打底：导入产物为初始 token 草稿，请经 compile_tokens 标准化后再保存为正式设计系统。'

# github 仓常见全局样式入口（命中即扫，借鉴前端仓约定俗成布局）
_GITHUB_CSS_CANDIDATES = (
    'apps/v4/app/globals.css',
    'app/globals.css',
    'src/app/globals.css',
    'src/index.css',
    'src/styles/globals.css',
    'styles/globals.css',
    'app/styles/globals.css',
    'src/styles/index.css',
    'apps/web/app/globals.css',
    'src/app.css',
)


@dataclass
class ImportResult:
    """导入草稿统一返回体。"""

    source_kind: str
    name: str
    tokens_css: str
    warnings: list[str] = field(default_factory=list)
    note: str = _DRAFT_NOTE

    def as_dict(self) -> dict:
        return {
            'source_kind': self.source_kind,
            'name': self.name,
            'tokens_css': self.tokens_css,
            'warnings': self.warnings,
            'note': self.note,
        }


# ── SSRF 闸 + 受限抓取 ────────────────────────────────────────────────────────
# IANA 基准测试段（RFC 2544/5180），公网从不作真实目的地；亦是 Clash/Surge 等
# 透明 TUN 代理的事实 fake-ip CIDR。生产直连环境 getaddrinfo 永不返回此段，故默认拒绝；
# 仅当部署在透明代理后（开发机）显式置 DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH 才放行。
_FAKEIP_NETS = (ipaddress.ip_network('198.18.0.0/15'),)


def _proxy_configured() -> bool:
    """是否配置了正向代理（HTTP(S)_PROXY/ALL_PROXY）。经代理出网时由代理管控目的地。"""
    return any(os.environ.get(k) for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'))


def _fakeip_passthrough() -> bool:
    return os.environ.get('DESIGNSYSTEM_IMPORT_FAKEIP_PASSTHROUGH', '').strip().lower() in {'1', 'true', 'yes'}


def _assert_scheme(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise errors.RequestError(msg=f'不支持的 URL 协议（仅 http/https）: {parsed.scheme or "缺失"}')
    if not parsed.hostname:
        raise errors.RequestError(msg='URL 缺少主机名')


def _assert_fetchable_url(url: str) -> None:
    """拒绝非 http/https、非全局可路由地址（私网/内网/回环/链路本地/保留全拒）。

    仅在**直连出网**（无正向代理）时调用：经代理出网时客户端 DNS 不决定目的地，
    IP 级预检不适用且会被 fake-ip 代理误伤；此时出网管控落在代理侧。
    """
    _assert_scheme(url)
    parsed = urlparse(url)
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except OSError as exc:
        raise errors.RequestError(msg=f'无法解析主机: {host}') from exc
    allow_fakeip = _fakeip_passthrough()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if allow_fakeip and any(ip in net for net in _FAKEIP_NETS):
            log.debug(f'designsystem import: 透明代理 fake-ip 放行 {ip}（{host}）')
            continue
        if not ip.is_global:
            raise errors.RequestError(msg=f'拒绝访问非公网地址: {ip}（{host}）')


async def _fetch_text(url: str, *, max_bytes: int) -> str:
    """受限抓取：禁重定向 + 超时 + 大小封顶；非 200 / 超体量如实报错。

    直连出网（无代理）时跑 IP 级 SSRF 守卫并禁用 env 代理（确定性）；
    有正向代理时放行经代理出网（trust_env=True），守卫落代理侧。
    """
    proxied = _proxy_configured()
    if proxied:
        _assert_scheme(url)
    else:
        _assert_fetchable_url(url)
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, trust_env=proxied, follow_redirects=False) as client:
            resp = await client.get(url, headers={'User-Agent': _USER_AGENT, 'Accept': '*/*'})
    except httpx.HTTPError as exc:
        raise errors.RequestError(msg=f'拉取失败: {url}（{exc}）') from exc
    if resp.status_code != 200:
        raise errors.RequestError(msg=f'拉取失败 HTTP {resp.status_code}: {url}')
    raw = resp.content
    if len(raw) > max_bytes:
        raise errors.RequestError(msg=f'响应过大（>{max_bytes} 字节）: {url}')
    return resp.text


# ── 公用：CSS 声明渲染（颜色三元组包回函数保真）────────────────────────────────
_COLOR_FUNC_RE = re.compile(r'^(#|rgb|hsl|hwb|oklch|oklab|lab|lch|color|var|color-mix|calc)', re.IGNORECASE)
# shadcn 旧版 cssVars 是裸 HSL 三元组（如 "0 0% 100%"），用作 hsl(var(--x))；包回 hsl(...) 才能被识别。
_HSL_TRIPLET_RE = re.compile(r'^[\d.]+\s+[\d.]+%\s+[\d.]+%(\s*/\s*[\d.]+%?)?$')
_KEY_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _wrap_value(value: str) -> str:
    v = value.strip()
    if _COLOR_FUNC_RE.match(v):
        return v
    if _HSL_TRIPLET_RE.match(v):
        return f'hsl({v})'
    return v


def _declaration(key: str, value: object) -> str | None:
    """渲染单条 `--k: v;`；非法键名或含分隔符的危险值跳过（不造假、不破坏块）。"""
    k = str(key).strip().lstrip('-')
    if not _KEY_RE.match(k):
        return None
    sval = str(value).strip()
    if not sval or any(ch in sval for ch in (';', '{', '}', '\n')):
        return None
    return f'  --{k}: {_wrap_value(sval)};'


def _render_root(pairs: list[tuple[str, object]], *, dark: list[tuple[str, object]] | None = None) -> str:
    root_lines: list[str] = []
    seen: set[str] = set()
    for key, value in pairs:
        norm = str(key).strip().lstrip('-')
        if norm in seen:
            continue
        decl = _declaration(key, value)
        if decl:
            root_lines.append(decl)
            seen.add(norm)
    if not root_lines:
        raise errors.RequestError(msg='未能从来源提取到任何可用的设计 token（草稿失败，未造假）')
    css = ':root {\n' + '\n'.join(root_lines) + '\n}\n'
    if dark:
        dark_lines = [d for d in starmap(_declaration, dark) if d]
        if dark_lines:
            css += '\n.dark {\n' + '\n'.join(dark_lines) + '\n}\n'
    return css


# ── 入口一：shadcn registry item ─────────────────────────────────────────────
async def import_shadcn(ref: str) -> ImportResult:
    """ref 为 registry item 的 https URL（registry JSON 带 cssVars: theme/light/dark）。"""
    ref = (ref or '').strip()
    if not ref:
        raise errors.RequestError(msg='shadcn 导入需要 registry item 的 URL')
    url = ref if ref.lower().startswith(('http://', 'https://')) else _resolve_shadcn_shorthand(ref)
    import json

    text = await _fetch_text(url, max_bytes=_MAX_DOC_BYTES)
    try:
        item = json.loads(text)
    except json.JSONDecodeError as exc:
        raise errors.RequestError(msg=f'shadcn registry item 非合法 JSON: {url}') from exc
    css_vars = item.get('cssVars') if isinstance(item, dict) else None
    if not isinstance(css_vars, dict):
        raise errors.RequestError(msg='该 shadcn registry item 不含 cssVars（无法导出 token 草稿）')

    pairs: list[tuple[str, object]] = []
    for group in ('theme', 'light'):
        block = css_vars.get(group)
        if isinstance(block, dict):
            pairs.extend(block.items())
    dark_block = css_vars.get('dark')
    dark_pairs = list(dark_block.items()) if isinstance(dark_block, dict) else None
    tokens_css = _render_root(pairs, dark=dark_pairs)
    name = str(item.get('name') or '').strip() or _hostname_label(url)
    return ImportResult(source_kind='imported_shadcn', name=name, tokens_css=tokens_css)


def _resolve_shadcn_shorthand(ref: str) -> str:
    """`<owner>/<repo>/<item>` → raw.github 候选（尽力而为；推荐直接传 registry item URL）。"""
    parts = [p for p in ref.split('/') if p]
    if len(parts) < 3:
        raise errors.RequestError(msg='shadcn 简写需 <owner>/<repo>/<item> 或直接传 registry item URL')
    owner, repo, item = parts[0], parts[1], '/'.join(parts[2:])
    item = item if item.endswith('.json') else f'{item}.json'
    return f'https://raw.githubusercontent.com/{owner}/{repo}/main/{item}'


# ── 入口二：github 前端仓 CSS 自定义属性 ──────────────────────────────────────
_CUSTOM_PROP_RE = re.compile(r'--([A-Za-z0-9_-]+)\s*:\s*([^;{}]+?)\s*;')


async def import_github(ref: str) -> ImportResult:
    """ref 为 `owner/repo[#branch]` 或指向某 .css 的 raw URL；扫 CSS 自定义属性收敛成草稿。"""
    ref = (ref or '').strip()
    if not ref:
        raise errors.RequestError(msg='github 导入需要 owner/repo 或 .css 的 raw URL')

    warnings: list[str] = []
    if ref.lower().startswith(('http://', 'https://')):
        text = await _fetch_text(ref, max_bytes=_MAX_FILE_BYTES)
        pairs = _scan_css_custom_props(text)
        name = _hostname_label(ref)
    else:
        owner, repo, branch = _parse_github_repo(ref)
        pairs, hit = await _scan_github_repo(owner, repo, branch, warnings)
        if not pairs:
            raise errors.RequestError(
                msg=f'未在 {owner}/{repo}@{branch} 的常见全局样式入口找到 CSS 自定义属性（草稿失败，未造假）'
            )
        warnings.append(f'命中样式文件: {hit}')
        name = repo

    tokens_css = _render_root(pairs)
    return ImportResult(source_kind='imported_github', name=name, tokens_css=tokens_css, warnings=warnings)


def _parse_github_repo(ref: str) -> tuple[str, str, str]:
    head, _, branch = ref.partition('#')
    parts = [p for p in head.split('/') if p]
    if len(parts) < 2:
        raise errors.RequestError(msg='github 简写需 owner/repo[#branch]')
    return parts[0], parts[1], (branch.strip() or 'main')


async def _scan_github_repo(
    owner: str, repo: str, branch: str, warnings: list[str]
) -> tuple[list[tuple[str, object]], str]:
    """按候选清单逐个探测 raw CSS，命中即扫；记录已尝试。"""
    first_fetch_error: errors.RequestError | None = None
    for path in _GITHUB_CSS_CANDIDATES[:_MAX_GITHUB_FILES]:
        raw_url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}'
        try:
            text = await _fetch_text(raw_url, max_bytes=_MAX_FILE_BYTES)
        except errors.RequestError as exc:
            # 404 仅表示当前候选路径不存在，可继续探测；超时、限流和服务端错误则保留。
            # 若所有候选都未命中，必须如实暴露出站故障，不能误报为“仓库没有 CSS”。
            if 'HTTP 404:' not in str(exc) and first_fetch_error is None:
                first_fetch_error = exc
            continue
        pairs = _scan_css_custom_props(text)
        if pairs:
            return pairs, path
        warnings.append(f'{path} 命中但无自定义属性')
    if first_fetch_error is not None:
        raise first_fetch_error
    return [], ''


def _scan_css_custom_props(text: str) -> list[tuple[str, object]]:
    """提取 CSS 自定义属性声明，保持首次出现顺序去重（草稿保真原始命名）。"""
    pairs: list[tuple[str, object]] = []
    seen: set[str] = set()
    for match in _CUSTOM_PROP_RE.finditer(text):
        key, value = match.group(1), match.group(2).strip()
        if key in seen:
            continue
        if value.startswith('var('):
            # 跳过别名引用（多为 Tailwind v4 @theme inline 的 --color-x: var(--x) 自引用镜像）；
            # 真值在 :root 首层定义处会被另行命中。草稿保留首层真值、不留自引用噪声。
            continue
        pairs.append((key, value))
        seen.add(key)
    return pairs


# ── 入口三：screenshot / URL（尽力而为，失败如实报错）─────────────────────────
_HEX_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b')
_RGB_RE = re.compile(r'rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}', re.IGNORECASE)
_FONT_RE = re.compile(r'font-family\s*:\s*([^;{}]+)[;}]', re.IGNORECASE)
_LINK_CSS_RE = re.compile(r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*>', re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


async def import_screenshot(ref: str) -> ImportResult:
    """ref 为页面 URL；抓 HTML + 外链 CSS，扫主色/字体出最小草稿（云端无头浏览器→以抓页近似截图）。"""
    ref = (ref or '').strip()
    if not ref.lower().startswith(('http://', 'https://')):
        raise errors.RequestError(msg='screenshot/url 导入需要页面 URL（http/https）')

    html = await _fetch_text(ref, max_bytes=_MAX_DOC_BYTES)
    corpus = html
    warnings: list[str] = []
    corpus += await _gather_linked_css(ref, html, warnings)

    hexes = [_normalize_hex(h) for h in _HEX_RE.findall(corpus)]
    hexes = [h for h in hexes if h]
    if not hexes:
        raise errors.RequestError(msg='未能从页面提取到任何十六进制主色（草稿失败，未造假）')

    counts = Counter(hexes)
    bg, fg, accent = _classify_palette(counts)
    pairs: list[tuple[str, object]] = [('bg', bg), ('fg', fg), ('accent', accent)]
    font = _first_font(corpus)
    if font:
        pairs.append(('font-body', font))
    else:
        warnings.append('未识别到 font-family，字体留空交 compile 兜底')

    tokens_css = _render_root(pairs)
    warnings.append('截图/抓页扫色为近似草稿，主色判定可能偏差，请人工核对')
    return ImportResult(source_kind='imported_screenshot', name=_hostname_label(ref), tokens_css=tokens_css, warnings=warnings)


async def _gather_linked_css(page_url: str, html: str, warnings: list[str]) -> str:
    out: list[str] = []
    fetched = 0
    for tag in _LINK_CSS_RE.findall(html):
        if fetched >= _MAX_LINKED_CSS:
            break
        href_match = _HREF_RE.search(tag)
        if not href_match:
            continue
        css_url = urljoin(page_url, href_match.group(1))
        try:
            out.append(await _fetch_text(css_url, max_bytes=_MAX_FILE_BYTES))
            fetched += 1
        except errors.RequestError as exc:
            log.debug(f'screenshot 外链 CSS 抓取跳过 {css_url}: {exc}')
    return '\n'.join(out)


def _normalize_hex(value: str) -> str | None:
    h = value.lstrip('#')
    if len(h) in (3, 4):
        h = ''.join(ch * 2 for ch in h[:3])
    elif len(h) in (6, 8):
        h = h[:6]
    else:
        return None
    try:
        int(h, 16)
    except ValueError:
        return None
    return f'#{h.lower()}'


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(hex_color: str) -> float:
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hi, lo = max(r, g, b), min(r, g, b)
    if hi == 0:
        return 0.0
    return (hi - lo) / hi


def _classify_palette(counts: Counter) -> tuple[str, str, str]:
    """最亮→bg、最暗→fg、最高频且足够饱和→accent（近似，已在 warnings 提示偏差）。"""
    uniq = list(counts.keys())
    bg = max(uniq, key=_luminance)
    fg = min(uniq, key=_luminance)
    saturated = [c for c in uniq if _saturation(c) >= 0.25 and c not in (bg, fg)]
    if saturated:
        accent = max(saturated, key=lambda c: counts[c])
    else:
        accent = max((c for c in uniq if c not in (bg, fg)), key=lambda c: counts[c], default=bg)
    return bg, fg, accent


def _first_font(corpus: str) -> str | None:
    match = _FONT_RE.search(corpus)
    if not match:
        return None
    stack = match.group(1).strip().strip(';').strip()
    return stack or None


def _hostname_label(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.replace('www.', '')


# ── 统一分发 ──────────────────────────────────────────────────────────────────
_IMPORTERS = {
    'shadcn': import_shadcn,
    'github': import_github,
    'screenshot': import_screenshot,
    'url': import_screenshot,  # url 与 screenshot 同走抓页扫色
}


async def import_design_source(source: str, ref: str) -> dict:
    """三入口统一分发：source ∈ {shadcn, github, screenshot, url}，产出 tokens.css 草稿 dict。"""
    importer = _IMPORTERS.get((source or '').strip().lower())
    if not importer:
        raise errors.RequestError(msg=f'未知导入来源: {source}（支持 shadcn / github / screenshot）')
    result = await importer(ref)
    return result.as_dict()
