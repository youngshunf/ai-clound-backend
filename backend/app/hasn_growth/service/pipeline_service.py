from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.service.business_service import LeadAutomationBusinessService, lead_automation_business_service


class LeadAutomationPipelineService:
    """Pipeline facade kept separate from generated CRUD services."""

    def __init__(self, business_service: LeadAutomationBusinessService | None = None) -> None:
        self.business_service = business_service or lead_automation_business_service

    async def run_job(self, db: AsyncSession, job_id: int) -> dict[str, Any]:
        return await self.business_service.run_job(db, job_id)

    async def archive_expired(self, db: AsyncSession) -> int:
        return await self.business_service.archive_expired(db)


lead_automation_pipeline_service = LeadAutomationPipelineService()
