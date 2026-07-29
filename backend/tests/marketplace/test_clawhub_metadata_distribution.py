"""ClawHub 元数据联邦与上游制品分发契约测试。"""

from __future__ import annotations

import asyncio
import json
import threading

from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.marketplace.service.clawhub_sync_service import (
    ClawHubIdentityError,
    ClawHubSyncService,
    ClawHubUpstreamError,
    build_clawhub_download_url,
)


class _ClawHubContractHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict[str, list[str]]]] = []

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.requests.append((parsed.path, query))

        if parsed.path == '/api/v1/skills':
            self._json(
                {
                    'items': [
                        {
                            'slug': 'shared',
                            'displayName': '共享技能',
                            'summary': 'Alice 与 Bob 各自发布了同名技能',
                            'latestVersion': {'version': '1.0.0'},
                            'stats': {'downloads': 10, 'stars': 1},
                        },
                        {
                            'slug': 'shared',
                            'displayName': '共享技能',
                            'summary': 'Alice 与 Bob 各自发布了同名技能',
                            'latestVersion': {'version': '1.0.0'},
                            'stats': {'downloads': 10, 'stars': 1},
                        },
                    ],
                    'nextCursor': None,
                }
            )
            return

        if parsed.path == '/api/v1/search':
            self._json(
                {
                    'results': [
                        {'slug': 'shared', 'ownerHandle': 'alice', 'version': '1.2.0'},
                        {'slug': 'shared', 'ownerHandle': 'bob', 'version': '2.0.0'},
                        {'slug': 'shared-helper', 'ownerHandle': 'carol', 'version': '1.0.0'},
                    ]
                }
            )
            return

        if parsed.path == '/api/v1/skills/shared':
            owner = query.get('ownerHandle', [None])[0]
            if owner not in {'alice', 'bob'}:
                self._json({'code': 'AMBIGUOUS_SKILL_SLUG'}, status=409)
                return
            version = '1.2.0' if owner == 'alice' else '2.0.0'
            self._json(
                {
                    'skill': {
                        'slug': 'shared',
                        'displayName': f'{owner} 的共享技能',
                        'summary': f'{owner} 发布的技能',
                        'stats': {'downloads': 12, 'stars': 2},
                    },
                    'owner': {'handle': owner},
                    'latestVersion': {'version': version, 'changelog': '更新'},
                }
            )
            return

        if parsed.path == '/api/v1/skills/ambiguous':
            self._json({'code': 'AMBIGUOUS_SKILL_SLUG'}, status=409)
            return

        if parsed.path == '/api/v1/skills/unique':
            self._json(
                {
                    'skill': {
                        'slug': 'unique',
                        'displayName': '唯一技能',
                        'summary': '用于验证并发元数据预取',
                    },
                    'owner': {'handle': 'alice'},
                    'latestVersion': {'version': '1.2.3'},
                }
            )
            return

        if parsed.path in {
            '/api/v1/skills/demo/versions/1.2.3',
            '/api/v1/skills/unique/versions/1.2.3',
        }:
            if query.get('ownerHandle') != ['alice']:
                self._json({'error': 'ownerHandle required'}, status=400)
                return
            self._json(
                {
                    'version': {
                        'version': '1.2.3',
                        'files': [
                            {
                                'path': 'SKILL.md',
                                'size': 120,
                                'sha256': 'a' * 64,
                                'contentType': 'text/markdown',
                            },
                            {
                                'path': 'scripts/run.py',
                                'size': 80,
                                'sha256': 'b' * 64,
                                'contentType': 'text/x-python',
                            },
                        ],
                    }
                }
            )
            return

        if parsed.path == '/api/v1/skills/duplicate/versions/1.0.0':
            self._json(
                {
                    'version': {
                        'version': '1.0.0',
                        'files': [
                            {'path': 'SKILL.md', 'size': 1, 'sha256': 'a' * 64},
                            {'path': 'SKILL.md', 'size': 2, 'sha256': 'b' * 64},
                        ],
                    }
                }
            )
            return

        self.send_response(404)
        self.end_headers()

    def _json(self, payload: dict, *, status: int = 200) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


@pytest.fixture
def clawhub_contract_server() -> Generator[str, None, None]:
    _ClawHubContractHandler.requests = []
    server = HTTPServer(('127.0.0.1', 0), _ClawHubContractHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/api/v1'
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_download_url_uses_current_clawhub_contract() -> None:
    assert build_clawhub_download_url(
        'https://clawhub.ai/api/v1',
        owner_handle='alice team',
        slug='demo',
        version='1.2.3',
    ) == (
        'https://clawhub.ai/api/v1/download'
        '?slug=demo&version=1.2.3&ownerHandle=alice+team'
    )


def test_fetch_all_skills_uses_limit_and_expands_duplicate_slug(
    clawhub_contract_server: str,
) -> None:
    service = ClawHubSyncService()
    service.clawhub_api_url = clawhub_contract_server

    skills = asyncio.run(service._fetch_all_skills())

    assert {(skill['ownerHandle'], skill['slug']) for skill in skills} == {
        ('alice', 'shared'),
        ('bob', 'shared'),
    }
    assert {
        (skill['ownerHandle'], skill['latestVersion']['version']) for skill in skills
    } == {('alice', '1.2.0'), ('bob', '2.0.0')}
    list_queries = [
        query
        for path, query in _ClawHubContractHandler.requests
        if path == '/api/v1/skills'
    ]
    assert len(list_queries) == 1
    assert list_queries[0]['limit'] == ['100']
    assert 'pageSize' not in list_queries[0]


def test_ambiguous_owner_is_explicit_error_not_community_fallback(
    clawhub_contract_server: str,
) -> None:
    service = ClawHubSyncService()
    service.clawhub_api_url = clawhub_contract_server

    with pytest.raises(ClawHubIdentityError, match='ambiguous'):
        asyncio.run(service._get_skill_owner('ambiguous'))


def test_fetch_version_metadata_keeps_file_manifest_without_downloading_package(
    clawhub_contract_server: str,
) -> None:
    service = ClawHubSyncService()
    service.clawhub_api_url = clawhub_contract_server

    version = asyncio.run(
        service._fetch_version_metadata(
            owner_handle='alice',
            slug='demo',
            version='1.2.3',
        )
    )

    assert version['version'] == '1.2.3'
    assert version['file_size'] == 200
    assert version['files'] == [
        {
            'path': 'SKILL.md',
            'size': 120,
            'sha256': 'a' * 64,
            'contentType': 'text/markdown',
        },
        {
            'path': 'scripts/run.py',
            'size': 80,
            'sha256': 'b' * 64,
            'contentType': 'text/x-python',
        },
    ]
    assert len(version['content_hash']) == 64
    assert version['package_url'].endswith(
        '/download?slug=demo&version=1.2.3&ownerHandle=alice'
    )


def test_distribution_batch_resolves_owner_and_version_with_shared_http_client(
    clawhub_contract_server: str,
) -> None:
    service = ClawHubSyncService()
    service.clawhub_api_url = clawhub_contract_server

    prepared, errors = asyncio.run(
        service._prepare_distribution_batch(
            [
                (
                    {
                        'slug': 'unique',
                        'displayName': '唯一技能',
                        'summary': '用于验证并发元数据预取',
                        'latestVersion': {'version': '1.2.3'},
                    },
                    None,
                )
            ]
        )
    )

    assert errors == []
    assert len(prepared) == 1
    skill, existing = prepared[0]
    assert existing is None
    assert skill['ownerHandle'] == 'alice'
    assert skill['_distribution_version']['file_size'] == 200
    assert not any(
        path == '/api/v1/download'
        for path, _query in _ClawHubContractHandler.requests
    )


def test_version_manifest_rejects_duplicate_paths(
    clawhub_contract_server: str,
) -> None:
    service = ClawHubSyncService()
    service.clawhub_api_url = clawhub_contract_server

    with pytest.raises(ClawHubUpstreamError, match='路径重复'):
        asyncio.run(
            service._fetch_version_metadata(
                owner_handle='alice',
                slug='duplicate',
                version='1.0.0',
            )
        )
