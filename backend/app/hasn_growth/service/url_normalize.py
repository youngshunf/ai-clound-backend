"""URL 规范化纯函数（URL 级去重的基础；doc08 §4.4）。

把同一资源的不同写法归一到唯一 normalized_url + sha256 url_hash，用于抓取前查重。
规则：小写 scheme/host、去 www.、去 fragment、根路径去末尾斜杠、剔除追踪参数(utm_*/fbclid/...)、query 参数排序。
纯函数无副作用、无 DB/网络依赖，便于单测。
"""

from __future__ import annotations

import hashlib

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 追踪参数（广告/分析回流标记，不影响页面实际内容）——规范化时剔除，避免同页因不同追踪参数被当作不同 URL。
_TRACKING_PREFIXES = ('utm_',)
_TRACKING_KEYS = frozenset(
    {
        'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid', 'yclid', 'spm', 'scm',
        'ref', 'ref_src', 'refsource', '_hsenc', '_hsmi', 'mc_cid', 'mc_eid', 'igshid',
    }
)


def _clean_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (key, value)
        for key, value in pairs
        if not key.lower().startswith(_TRACKING_PREFIXES) and key.lower() not in _TRACKING_KEYS
    ]
    kept.sort()
    return urlencode(kept)


def normalize_url(raw: str | None) -> tuple[str, str, str | None] | None:
    """规范化 URL，返回 ``(normalized_url, url_hash, domain)``；无法解析返回 None。

    domain 为去端口的纯域名（已去 www.），用于按域统计/限频。
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if '://' not in text:
        text = f'https://{text}'
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if not parsed.netloc:
        return None

    scheme = (parsed.scheme or 'https').lower()
    netloc = parsed.netloc.lower()
    userinfo = ''
    hostport = netloc
    if '@' in netloc:
        userinfo, hostport = netloc.rsplit('@', 1)
        userinfo += '@'
    hostport = hostport.removeprefix('www.')
    netloc = userinfo + hostport

    path = parsed.path
    if path == '/':
        path = ''
    elif path.endswith('/') and len(path) > 1:
        path = path.rstrip('/')

    query = _clean_query(parsed.query)
    normalized = urlunparse((scheme, netloc, path, '', query, ''))  # 去 params/fragment
    url_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    domain = hostport.split(':', 1)[0] or None
    return normalized, url_hash, domain
