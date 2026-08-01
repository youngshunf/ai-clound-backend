"""技能包 Runtime 权威快照 Agent API。

Runtime 只提交固定 ``package_id + version + content_hash`` 引用；本端按已验证 Agent
凭证的主人归属判权，返回精确版本 definition，不让调用方上传 ``hermes_yaml``。
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa

from fastapi import APIRouter, Query

from backend.app.marketplace.service import skill_pack_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/{package_id:path}', summary='读取技能包固定版本 Runtime 权威快照')
async def get_runtime_skill_pack_authority(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    package_id: str,
    version: Annotated[str, Query(min_length=1, max_length=64)],
) -> ResponseSchemaModel[dict[str, Any]]:
    row = (
        (
            await db.execute(
                sa.text(
                    """
                    SELECT t.template_id, v.version, v.bundle_slug, v.command_key,
                           v.hermes_yaml, v.skill_dependencies_versioned,
                           COALESCE(v.content_hash, v.file_hash) AS content_hash
                    FROM hasn_marketplace.marketplace_template t
                    JOIN hasn_marketplace.marketplace_template_version v
                      ON v.template_id = t.template_id
                    WHERE t.template_id = :package_id
                      AND v.version = :version
                      AND t.template_type = 'skill_pack'
                      AND v.hermes_yaml IS NOT NULL
                      AND (
                        t.author_id = :owner_user_id
                        OR t.user_id = :owner_user_id
                        OR (
                          t.status = 'published'
                          AND (t.is_private = false OR t.is_official = true)
                        )
                      )
                    LIMIT 1
                    """
                ),
                {
                    'package_id': package_id,
                    'version': version,
                    'owner_user_id': agent.owner_user_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not row.get('content_hash'):
        raise errors.NotFoundError(msg='runtime_skill_bundle_unavailable')

    hermes_yaml = str(row['hermes_yaml'])
    member_ids = skill_pack_service.member_skill_ids(hermes_yaml)
    member_skills = await skill_pack_service.resolve_member_skill_snapshots(
        db,
        member_ids,
        row.get('skill_dependencies_versioned'),
    )
    return response_base.success(
        data={
            'package_id': row['template_id'],
            'version': row['version'],
            'bundle_slug': row['bundle_slug'],
            'command_key': row['command_key'],
            'hermes_yaml': hermes_yaml,
            'content_hash': skill_pack_service.content_hash(hermes_yaml),
            'member_skill_ids': member_ids,
            'member_skills': member_skills,
        }
    )
