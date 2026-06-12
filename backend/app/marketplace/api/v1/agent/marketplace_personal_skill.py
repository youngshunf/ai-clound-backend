"""个人技能库 agent API（Agent JWT，SKILLSYNC-C2 难点5）。

私有个人技能的**带鉴权、直回字节**下载端点，供 hermes provisioning 跨设备物化。
区别于 open 端点 302 跳私有桶 URL（urllib 取私有技能会 401）：此处用 Agent JWT 校验
owner 归属后 StreamingResponse 直接回 zip 字节。
"""

from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.app.marketplace.service.marketplace_personal_skill_service import personal_skill_service
from backend.common.security.agent_jwt import AgentTokenPayload
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession  # noqa: TC001

router = APIRouter()


@router.get('/{personal_skill_id:path}/download', summary='Download private personal skill package (agent)')
async def download_personal_skill(
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    db: CurrentSession,
    personal_skill_id: str,
) -> StreamingResponse:
    # owner 归属由 agent JWT 的 owner_user_id 决定：只能下载本 owner 名下的个人技能。
    skill, content = await personal_skill_service.get_package_for_owner(
        db, owner_user_id=agent.owner_user_id, personal_skill_id=personal_skill_id
    )
    filename = f'{skill.slug}.zip'
    return StreamingResponse(
        BytesIO(content),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
