"""ClawHub 429 退避、超时断点与续跑测试（真实 HTTP + PostgreSQL）。"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model import (
    MarketplaceSkill,
    MarketplaceSkillVersion,
    MarketplaceSyncLog,
)
from backend.app.marketplace.service.clawhub_sync_service import ClawHubSyncService
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).parents[2]
    / 'sql/marketplace/migrations/2026-08-02-clawhub-sync-resume-cursor.sql'
)


def _start_catalog_service(item: dict[str, object]):
    state = SimpleNamespace(phase='rate_limit', cursors=[], transient_requests=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            cursor = parse_qs(parsed.query).get('cursor', [None])[0]
            state.cursors.append(cursor)
            if parsed.path != '/api/v1/skills':
                self.send_error(404)
                return
            if cursor == 'cursor-after-page-1' and state.phase == 'rate_limit':
                state.transient_requests += 1
                self.send_response(429)
                self.send_header('Retry-After', '0.05')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            payload = (
                {'items': [item], 'nextCursor': 'cursor-after-page-1'}
                if cursor is None
                else {'items': [item], 'nextCursor': None}
            )
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, state


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        for statement in _MIGRATION.read_text(encoding='utf-8').split('-- statement-breakpoint'):
            if statement.strip():
                await session.execute(sa.text(statement))
        await session.commit()
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_rate_limit_timeout_persists_cursor_and_next_run_resumes_without_unpublishing(
    db_session,
) -> None:
    tag = uuid.uuid4().hex[:10]
    skill_id = f'clawhub/alice/resume-{tag}'
    untouched_id = f'clawhub/alice/untouched-{tag}'
    manifest = json.dumps(
        [{'path': 'SKILL.md', 'size': 10, 'sha256': 'a' * 64}],
        ensure_ascii=False,
    )
    created_log_ids: list[int] = []
    db_session.add_all(
        [
            MarketplaceSkill(
                skill_id=skill_id,
                namespace='clawhub/alice',
                slug=f'resume-{tag}',
                name=f'Resume {tag}',
                description_en='Resume test',
                source_language='en',
                files=manifest,
                status='published',
                visibility='public',
                source_type='clawhub',
                author_name='alice',
            ),
            MarketplaceSkill(
                skill_id=untouched_id,
                namespace='clawhub/alice',
                slug=f'untouched-{tag}',
                name=f'Untouched {tag}',
                description_en='Must remain published',
                source_language='en',
                files=manifest,
                status='published',
                visibility='public',
                source_type='clawhub',
                author_name='alice',
            ),
            MarketplaceSkillVersion(
                skill_id=skill_id,
                version='1.0.0',
                is_latest=True,
            ),
        ]
    )
    await db_session.commit()

    item: dict[str, object] = {
        'slug': f'resume-{tag}',
        'ownerHandle': 'alice',
        'displayName': f'Resume {tag}',
        'summary': 'Resume test',
        'latestVersion': {'version': '1.0.0'},
        'stats': {'downloads': 10, 'stars': 1},
    }
    server, thread, state = _start_catalog_service(item)
    service = ClawHubSyncService()
    service.clawhub_api_url = f'http://127.0.0.1:{server.server_port}/api/v1'
    service.overall_timeout_seconds = 0.25
    service.transient_max_delay_seconds = 0.05
    try:
        first = await service.sync_from_clawhub(db_session, limit=0)
        created_log_ids.append(first['sync_log_id'])
        assert first['timed_out'] is True
        assert first['resume_cursor'] == 'cursor-after-page-1'
        untouched = await db_session.scalar(
            sa.select(MarketplaceSkill).where(MarketplaceSkill.skill_id == untouched_id)
        )
        assert untouched is not None and untouched.status == 'published'

        state.phase = 'success'
        state.cursors.clear()
        service.overall_timeout_seconds = 5
        second = await service.sync_from_clawhub(db_session, limit=0)
        created_log_ids.append(second['sync_log_id'])
        assert second['timed_out'] is False
        assert second['resume_cursor'] is None
        assert state.cursors[0] == 'cursor-after-page-1'
        untouched = await db_session.scalar(
            sa.select(MarketplaceSkill).where(MarketplaceSkill.skill_id == untouched_id)
        )
        assert untouched is not None and untouched.status == 'published'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        await db_session.rollback()
        await db_session.execute(
            sa.delete(MarketplaceSkillVersion).where(
                MarketplaceSkillVersion.skill_id.in_([skill_id, untouched_id])
            )
        )
        await db_session.execute(
            sa.delete(MarketplaceSkill).where(MarketplaceSkill.skill_id.in_([skill_id, untouched_id]))
        )
        if created_log_ids:
            await db_session.execute(
                sa.delete(MarketplaceSyncLog).where(MarketplaceSyncLog.id.in_(created_log_ids))
            )
        await db_session.commit()


async def test_catalog_page_keeps_retrying_transient_http_beyond_three_attempts() -> None:
    item: dict[str, object] = {'slug': 'retry-only'}
    server, thread, state = _start_catalog_service(item)
    state.phase = 'rate_limit'
    service = ClawHubSyncService()
    assert service.catalog_concurrency == 4
    service.clawhub_api_url = f'http://127.0.0.1:{server.server_port}/api/v1'
    service.transient_max_delay_seconds = 0
    try:
        async with httpx.AsyncClient() as client:
            task = asyncio.create_task(
                service._fetch_catalog_page(
                    client,
                    cursor='cursor-after-page-1',
                    page_index=1,
                )
            )
            while state.transient_requests < 4:
                await asyncio.sleep(0.01)
            state.phase = 'success'
            items, next_cursor = await asyncio.wait_for(task, timeout=2)
        assert items == [item]
        assert next_cursor is None
        assert state.transient_requests >= 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
