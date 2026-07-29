"""商机 Agent 写点的统一 register-on-write。"""

from __future__ import annotations

import hashlib

from typing import Any, Literal

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.pii_boundary import assert_growth_pii_payload_safe
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.common.dataclasses import AgentTokenPayload


async def register_opportunity_artifact(
    db: AsyncSession,
    *,
    agent: AgentTokenPayload,
    opportunity: dict[str, Any],
    source_tool: str,
    idempotency_key: str,
    action: Literal['create', 'update'],
) -> ArtifactRegistration | None:
    """每次分身创建、推进或关闭商机时登记同一稳定资源。"""
    opportunity_id = opportunity.get('id')
    growth_project_id = opportunity.get('growth_project_id')
    if not isinstance(opportunity_id, int):
        return None
    registration_options: dict[str, Any] = {}
    if isinstance(growth_project_id, str):
        project = (
            await db.execute(
                sa.select(GrowthProject).where(
                    GrowthProject.id == growth_project_id,
                )
            )
        ).scalar_one_or_none()
        if project is None:
            return None
        registration_options['project_id'] = str(project.platform_project_id)
    title = str(redact_pii_value(opportunity.get('name') or '获客商机'))
    assert_growth_pii_payload_safe({
        'title': title,
        'opportunity_id': opportunity_id,
        'growth_project_id': growth_project_id,
    })
    project_key = growth_project_id if isinstance(growth_project_id, str) else 'unscoped'
    dispatch_digest = hashlib.sha256(f'{project_key}:{opportunity_id}:{idempotency_key}'.encode()).hexdigest()[:40]
    return await register_app_resource_artifact(
        db,
        app_id='growth',
        resource_kind='growth.opportunity',
        server_id=opportunity_id,
        agent_hasn_id=agent.agent_hasn_id,
        owner_hasn_id=agent.owner_hasn_id,
        title=title,
        source_tool=source_tool,
        action=action,
        dispatch_id=f'growth-opportunity:{dispatch_digest}',
        metadata={
            'stage': opportunity.get('stage'),
            'version': opportunity.get('version'),
        },
        **registration_options,
    )
