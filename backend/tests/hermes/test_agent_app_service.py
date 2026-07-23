"""Hermes Agent 模板解析的真实 PostgreSQL 集成测试。"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hermes.service.hermes_agent_app_service import HermesAgentAppService
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _template_id() -> str:
    return f'huanxing/hermes-template-{uuid.uuid4().hex[:12]}'


async def test_resolve_template_uses_latest_agent_template_from_postgresql() -> None:
    """模板解析只接受 Agent 类型的最新版本，并返回稳定的 template_id。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    template_id = _template_id()
    try:
        await session.execute(
            text(
                """
                INSERT INTO hasn_marketplace.marketplace_template (
                    template_id, namespace, slug, template_type, name, description,
                    author_id, pricing_type, price, is_private, is_official, status,
                    download_count, source_type, created_time, updated_time
                )
                VALUES (
                    :template_id, 'huanxing', :slug, 'agent', '集成测试模板', '验证模板解析契约',
                    970001, 'free', 0, false, true, 'published', 0, 'local', now(), now()
                )
                """
            ),
            {'template_id': template_id, 'slug': template_id.rsplit('/', 1)[-1]},
        )
        await session.execute(
            text(
                """
                INSERT INTO hasn_marketplace.marketplace_template_version (
                    template_id, version, package_url, file_hash, content_hash, is_latest,
                    published_at, created_time, updated_time
                )
                VALUES (
                    :template_id, '1.0.0', 'https://example.invalid/hermes-template.tar.gz',
                    'sha256:test', 'sha256:test', true, now(), now(), now()
                )
                """
            ),
            {'template_id': template_id},
        )
        await session.flush()

        resolved = await HermesAgentAppService()._resolve_template(session, template_id)

        assert resolved['template_id'] == template_id
        assert resolved['version'] == '1.0.0'
        assert resolved['package_url'] == 'https://example.invalid/hermes-template.tar.gz'
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
