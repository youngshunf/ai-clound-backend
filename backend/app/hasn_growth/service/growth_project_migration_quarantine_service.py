"""获客项目迁移隔离清单的受控写入服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_growth.model.growth_project_migration_quarantine import (
    GrowthProjectMigrationQuarantine,
)
from backend.app.hasn_growth.service.pii_boundary import assert_growth_pii_payload_safe

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class GrowthProjectMigrationQuarantineService:
    """只允许幂等追加迁移异常，禁止业务接口直接删除审计依据。"""

    @staticmethod
    async def record(
        db: AsyncSession,
        *,
        source_table: str,
        source_record_id: str,
        reason_code: str,
        owner_scope_hint: str | None,
        user_id_hint: int | None,
        enterprise_id_hint: int | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = details or {}
        assert_growth_pii_payload_safe(safe_details)
        await db.execute(
            pg_insert(GrowthProjectMigrationQuarantine)
            .values(
                source_table=source_table,
                source_record_id=source_record_id,
                reason_code=reason_code,
                owner_scope_hint=owner_scope_hint,
                user_id_hint=user_id_hint,
                enterprise_id_hint=enterprise_id_hint,
                details=safe_details,
                status='pending',
            )
            .on_conflict_do_nothing(
                constraint='uq_growth_project_migration_quarantine_source'
            )
        )


growth_project_migration_quarantine_service = (
    GrowthProjectMigrationQuarantineService()
)
