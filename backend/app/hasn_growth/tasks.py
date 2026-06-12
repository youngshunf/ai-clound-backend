from __future__ import annotations

import asyncio

from backend.app.hasn_growth.service.pipeline_service import lead_automation_pipeline_service
from backend.database.db import async_db_session


async def _run_job(job_id: int) -> dict:
    async with async_db_session.begin() as db:
        return await lead_automation_pipeline_service.run_job(db, job_id)


async def _archive_expired() -> int:
    async with async_db_session.begin() as db:
        return await lead_automation_pipeline_service.archive_expired(db)


def lead_automation_run_job(job_id: int) -> dict:
    """Run a lead automation collection job."""

    return asyncio.run(_run_job(job_id))


def lead_automation_archive_expired() -> dict[str, int]:
    """Archive expired lead contacts."""

    return {'archived_count': asyncio.run(_archive_expired())}
