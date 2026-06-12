"""ClawHub 同步：下载量阈值 + 50G 磁盘闸 + dry-run 报告 + cursor 分页 单测。

锁住本次新增契约（对应"下载量超过 100 才同步" + "占用达 50G 暂停" 需求）：
- ``_filter_skills`` 的 downloads 阈值是 **严格大于**；threshold<=0 表示不过滤（全收，向后兼容）。
- ``_dir_size_bytes`` 真实递归统计目录占用（磁盘闸度量），目录不存在 -> 0。
- ``_build_dry_run_report`` 只读不写、命中数量/占用预估字段齐全。
- ``_fetch_all_skills`` 用 **cursor 游标**翻页（不是 page/pageSize），用真实本地 HTTP stub 验证
  （零 mock：起一个真的 http.server 喂分页数据）。
"""

from __future__ import annotations

import asyncio
import json
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from backend.app.marketplace.service.clawhub_sync_service import ClawHubSyncService

if TYPE_CHECKING:
    from pathlib import Path


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


# ── 磁盘大小度量（50G 闸的核心） ─────────────────────────────────────────────
def test_dir_size_bytes_sums_files(tmp_path: Path) -> None:
    (tmp_path / 'a.txt').write_bytes(b'x' * 100)
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'b.txt').write_bytes(b'y' * 250)
    assert ClawHubSyncService._dir_size_bytes(tmp_path) == 350


def test_dir_size_bytes_missing_dir_is_zero(tmp_path: Path) -> None:
    assert ClawHubSyncService._dir_size_bytes(tmp_path / 'nope') == 0


# ── dry-run 报告（只读不写） ─────────────────────────────────────────────────
def test_build_dry_run_report_counts_and_estimates(tmp_path: Path) -> None:
    svc = ClawHubSyncService()
    svc.hub_local_path = tmp_path  # 指向干净 tmp，shutil.disk_usage 真实可用
    # 生产里传入的是 _filter_skills 已按 downloads 降序排好的列表
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
    assert report['estimated_disk_gb'] >= 0
    assert report['max_disk_gb'] == 50.0
    assert report['top_by_downloads'][0] == {'slug': 'd', 'downloads': 955}


# ── cursor 游标分页（真实本地 HTTP stub，零 mock） ───────────────────────────
class _CursorPagesHandler(BaseHTTPRequestHandler):
    # 三页：无 cursor -> [s0,s1]+c1；cursor=c1 -> [s2,s3]+c2；cursor=c2 -> [s4]+无 cursor
    PAGES = {
        None: {'items': [_skill('s0', 1), _skill('s1', 2)], 'nextCursor': 'c1'},
        'c1': {'items': [_skill('s2', 3), _skill('s3', 4)], 'nextCursor': 'c2'},
        'c2': {'items': [_skill('s4', 5)], 'nextCursor': None},
    }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.endswith('/skills'):
            self.send_response(404)
            self.end_headers()
            return
        cursor = parse_qs(parsed.query).get('cursor', [None])[0]
        body = json.dumps(self.PAGES.get(cursor, {'items': [], 'nextCursor': None}))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, *args) -> None:  # 静音
        return


def test_fetch_all_skills_follows_cursor() -> None:
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
