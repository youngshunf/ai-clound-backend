"""S4 Growth provisioning 的真实 PostgreSQL + 真实 RAGFlow 全链路。"""

from __future__ import annotations

import uuid

from pathlib import Path

import pytest
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_provision import (
    GrowthProjectProvision,
)
from backend.app.hasn_growth.service.growth_project_app_service import (
    growth_project_app_service,
)
from backend.app.hasn_growth.service.growth_project_provision_service import (
    growth_project_provision_service,
)
from backend.app.hasn_knowledge.model.document import Document
from backend.app.hasn_knowledge.model.document_version import DocumentVersion
from backend.app.hasn_knowledge.model.folder import Folder
from backend.app.hasn_knowledge.model.kb import Kb
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_MIGRATION = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-30-growth-project-provision-state-machine.sql'


async def _apply_migration(db: AsyncSession) -> None:
    raw = await (await db.connection()).get_raw_connection()
    driver_connection = raw.driver_connection
    assert driver_connection is not None
    await driver_connection.execute(_MIGRATION.read_text(encoding='utf-8'))


async def test_growth_provision_creates_one_real_kb_and_resumes_without_duplicates() -> None:
    """四步全绿后进入 ready；重复 worker 不重复建库、目录或基础文档。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    owner = f'h_growth_provision_{tag}'
    platform_project_id = None
    growth_project_id = None
    kb_id = None
    try:
        async with maker() as db:
            await _apply_migration(db)
            platform = HasnProject(
                owner_id=owner,
                name=f'S4 真实开通 {tag}',
                goal='验证真实基础资源开通',
                status='active',
            )
            db.add(platform)
            await db.flush()
            platform_project_id = platform.id
            created = await growth_project_app_service.enable(
                db,
                owner_hasn_id=owner,
                owner_user_id=105,
                platform_project_id=platform.id,
                name=None,
                tagline='真实 RAGFlow',
                command_id=str(uuid.uuid4()),
                idempotency_key=f'growth-provision-real-{tag}',
            )
            growth_project_id = uuid.UUID(created['growth_project']['id'])
            await db.commit()

        result = await growth_project_provision_service.run(growth_project_id)
        assert result == {'status': 'ready'}

        async with maker() as db:
            growth = await db.get(GrowthProject, growth_project_id)
            assert growth is not None
            assert growth.status == 'active'
            assert growth.provision_status == 'ready'
            assert growth.kb_ref is not None
            kb = (
                await db.execute(
                    sa.select(Kb).where(
                        Kb.owner_id == owner,
                        Kb.client_request_id == f'growth:{growth_project_id}:knowledge',
                    )
                )
            ).scalar_one()
            kb_id = kb.id
            assert kb.platform_project_id == platform_project_id
            assert (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(Folder)
                    .where(
                        Folder.kb_id == kb.id,
                        Folder.deleted_time.is_(None),
                    )
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(Document)
                    .where(
                        Document.kb_id == kb.id,
                        Document.deleted_time.is_(None),
                    )
                )
            ).scalar_one() == 3
            assert (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(GrowthProjectProvision)
                    .where(
                        GrowthProjectProvision.growth_project_id == growth_project_id,
                        GrowthProjectProvision.status == 'success',
                    )
                )
            ).scalar_one() == 4

        replay = await growth_project_provision_service.run(growth_project_id)
        assert replay == {'status': 'ready'}
        async with maker() as db:
            assert (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(Kb)
                    .where(
                        Kb.owner_id == owner,
                        Kb.client_request_id == f'growth:{growth_project_id}:knowledge',
                    )
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(Document)
                    .where(
                        Document.kb_id == kb_id,
                        Document.deleted_time.is_(None),
                    )
                )
            ).scalar_one() == 3
    finally:
        if kb_id is not None:
            async with maker() as db:
                await knowledge_service.delete_kb(db, owner, kb_id)
                document_ids = sa.select(Document.id).where(Document.kb_id == kb_id)
                await db.execute(sa.delete(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids)))
                await db.execute(sa.delete(Document).where(Document.kb_id == kb_id))
                await db.execute(sa.delete(Folder).where(Folder.kb_id == kb_id))
                await db.execute(sa.delete(Kb).where(Kb.id == kb_id))
                await db.commit()
        if growth_project_id is not None:
            async with maker.begin() as db:
                await db.execute(
                    sa.delete(GrowthProjectProvision).where(
                        GrowthProjectProvision.growth_project_id == growth_project_id
                    )
                )
                await db.execute(sa.delete(GrowthProject).where(GrowthProject.id == growth_project_id))
        if platform_project_id is not None:
            async with maker.begin() as db:
                await db.execute(sa.delete(HasnProject).where(HasnProject.id == platform_project_id))
        await engine.dispose()
