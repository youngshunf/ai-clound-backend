"""ClawHub 元数据同步：下载量阈值、dry-run 报告与 cursor 分页测试。

锁住当前元数据联邦契约：
- ``_filter_skills`` 的 downloads 阈值是 **严格大于**；threshold<=0 表示不过滤（全收，向后兼容）。
- ``_build_dry_run_report`` 明确报告服务器包下载与磁盘占用均为零。
- ``_fetch_all_skills`` 用 **limit + cursor** 翻页，用真实本地 HTTP stub 验证
  （零 mock：起一个真的 http.server 喂分页数据）。
"""

from __future__ import annotations

import asyncio
import json
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from backend.app.marketplace.service.clawhub_sync_service import ClawHubSyncService
from backend.app.marketplace.service.skill_content_extractor import raw_bilingual_body


def _skill(slug: str, downloads: int, stars: int = 0, updated: int = 0) -> dict:
    return {
        'slug': slug,
        'stats': {'downloads': downloads, 'stars': stars},
        'updatedAt': updated,
    }


# ── downloads 阈值 ────────────────────────────────────────────────────────────
def test_filter_min_downloads_is_strictly_greater() -> None:
    svc = ClawHubSyncService()
    skills = [
        _skill('a', 50),
        _skill('b', 100),  # == 100 -> 被排除（"超过 100" = 严格 > 100）
        _skill('c', 101),
        _skill('d', 955),
        _skill('zero', 0),
    ]
    out = svc._filter_skills(skills, limit=0, min_downloads=100)
    slugs = {s['slug'] for s in out}
    assert slugs == {'c', 'd'}


def test_filter_threshold_zero_keeps_all_including_zero_downloads() -> None:
    # threshold<=0 -> 不过滤（向后兼容旧默认同步，含 downloads=0）。
    svc = ClawHubSyncService()
    skills = [_skill('a', 0), _skill('b', 5)]
    out = svc._filter_skills(skills, limit=0, min_downloads=0)
    assert {s['slug'] for s in out} == {'a', 'b'}


def test_filter_ranks_by_downloads_desc() -> None:
    svc = ClawHubSyncService()
    skills = [_skill('low', 120), _skill('high', 900), _skill('mid', 300)]
    out = svc._filter_skills(skills, limit=0, min_downloads=100)
    assert [s['slug'] for s in out] == ['high', 'mid', 'low']


def test_filter_limit_caps_after_threshold() -> None:
    svc = ClawHubSyncService()
    skills = [_skill(f's{i}', 100 + i * 10) for i in range(10)]
    out = svc._filter_skills(skills, limit=3, min_downloads=100)
    assert len(out) == 3
    # 取 downloads 最高的 3 个
    assert [s['slug'] for s in out] == ['s9', 's8', 's7']


# ── 真实人气过滤（require_engagement：downloads>0 或 stars>0） ────────────────
def test_require_engagement_drops_zero_zero_skills() -> None:
    # "按真实人气来"：丢弃 downloads=0 且 stars=0 的占位/冷门技能，
    # 但 downloads>0 或 stars>0 任一即保留。与 min_downloads=0（全收）正交叠加。
    svc = ClawHubSyncService()
    skills = [
        _skill('dead', 0, stars=0),  # 0/0 -> 丢
        _skill('starred', 0, stars=7),  # 仅有 star -> 留
        _skill('downloaded', 5, stars=0),  # 仅有下载 -> 留
        _skill('hot', 900, stars=12),  # 都有 -> 留
    ]
    out = svc._filter_skills(skills, limit=0, min_downloads=0, require_engagement=True)
    assert {s['slug'] for s in out} == {'starred', 'downloaded', 'hot'}


def test_require_engagement_default_off_keeps_zero_zero() -> None:
    # 不传 require_engagement（默认 False）时，min_downloads=0 仍是"全收"，含 0/0。
    svc = ClawHubSyncService()
    skills = [_skill('dead', 0, stars=0), _skill('hot', 5, stars=1)]
    out = svc._filter_skills(skills, limit=0, min_downloads=0)
    assert {s['slug'] for s in out} == {'dead', 'hot'}


def test_require_engagement_top_n_by_downloads_then_stars() -> None:
    # 取真实人气前 N：先丢 0/0，再按 (downloads, stars) 降序截前 N。
    svc = ClawHubSyncService()
    skills = [
        _skill('dead', 0, stars=0),
        _skill('a', 100, stars=1),
        _skill('b', 100, stars=9),  # downloads 同 a，stars 更高 -> 排在 a 前
        _skill('c', 300, stars=0),
    ]
    out = svc._filter_skills(skills, limit=2, min_downloads=0, require_engagement=True)
    assert [s['slug'] for s in out] == ['c', 'b']


# ── dry-run 报告（只读不写） ─────────────────────────────────────────────────
def test_build_dry_run_report_is_metadata_only() -> None:
    svc = ClawHubSyncService()
    filtered = [_skill('d', 955), _skill('c', 300)]
    report = svc._build_dry_run_report(
        total_fetched=10,
        filtered=filtered,
        min_downloads=100,
        limit=0,
    )
    assert report['dry_run'] is True
    assert report['matched'] == 2
    assert report['min_downloads'] == 100
    assert report['total_fetched'] == 10
    assert report['mode'] == 'metadata_only'
    assert report['estimated_package_download_bytes'] == 0
    assert report['estimated_server_disk_bytes'] == 0
    assert report['top_by_downloads'][0]['slug'] == 'd'
    assert report['top_by_downloads'][0]['downloads'] == 955


# ── cursor 游标分页（真实本地 HTTP stub，零 mock） ───────────────────────────
class _CursorPagesHandler(BaseHTTPRequestHandler):
    # 三页：无 cursor -> [s0,s1]+c1；cursor=c1 -> [s2,s3]+c2；cursor=c2 -> [s4]+无 cursor
    PAGES = {
        None: {'items': [_skill('s0', 1), _skill('s1', 2)], 'nextCursor': 'c1'},
        'c1': {'items': [_skill('s2', 3), _skill('s3', 4)], 'nextCursor': 'c2'},
        'c2': {'items': [_skill('s4', 5)], 'nextCursor': None},
    }
    QUERIES: list[dict[str, list[str]]] = []

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.endswith('/skills'):
            self.send_response(404)
            self.end_headers()
            return
        cursor = parse_qs(parsed.query).get('cursor', [None])[0]
        self.QUERIES.append(parse_qs(parsed.query))
        body = json.dumps(self.PAGES.get(cursor, {'items': [], 'nextCursor': None}))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, *args) -> None:  # 静音
        return


def test_fetch_all_skills_follows_cursor() -> None:
    _CursorPagesHandler.QUERIES = []
    server = HTTPServer(('127.0.0.1', 0), _CursorPagesHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        svc = ClawHubSyncService()
        svc.clawhub_api_url = f'http://127.0.0.1:{port}/api/v1'
        skills = asyncio.run(svc._fetch_all_skills())
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert [s['slug'] for s in skills] == ['s0', 's1', 's2', 's3', 's4']
    assert all(query['sort'] == ['downloads'] for query in _CursorPagesHandler.QUERIES)


def test_fetch_all_skills_stops_after_requested_limit() -> None:
    _CursorPagesHandler.QUERIES = []
    server = HTTPServer(('127.0.0.1', 0), _CursorPagesHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        svc = ClawHubSyncService()
        svc.clawhub_api_url = f'http://127.0.0.1:{port}/api/v1'
        skills = asyncio.run(svc._fetch_all_skills(limit=3))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert [skill['slug'] for skill in skills] == ['s0', 's1', 's2']
    assert len(_CursorPagesHandler.QUERIES) == 2
    assert all(query['sort'] == ['downloads'] for query in _CursorPagesHandler.QUERIES)


# ── 正文原文填充（translate_body=False 路径，零 LLM） ─────────────────────────
def test_raw_bilingual_body_english_keeps_original_on_en_side() -> None:
    # 英文正文 -> 原文落 body_en，body_zh 留 None（序列化器回退显示原文）。
    body_en, body_zh = raw_bilingual_body(None, 'This is a plain English readme body.')
    assert body_en == 'This is a plain English readme body.'
    assert body_zh is None


def test_raw_bilingual_body_chinese_keeps_original_on_zh_side() -> None:
    # 中文正文 -> 原文落 body_zh，body_en 留 None。
    text = '这是一段中文技能说明正文内容，用于验证原文填充落在中文侧。'
    body_en, body_zh = raw_bilingual_body(None, text)
    assert body_zh == text
    assert body_en is None


def test_raw_bilingual_body_empty_clears_both_sides() -> None:
    assert raw_bilingual_body(None, '   ') == (None, None)


def test_batch_prepare_metadata_empty_is_noop() -> None:
    # 空列表不触发任何 LLM，直接返回空映射（existing_by_slug 一并传空）。
    svc = ClawHubSyncService()
    assert asyncio.run(svc._batch_prepare_metadata([], {})) == {}
