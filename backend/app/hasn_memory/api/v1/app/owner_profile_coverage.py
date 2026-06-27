"""主人画像完整度 - 用户端（App scope）只读视图。

「了解主人」功能：主人查看分身对自己 5 个画像维度的了解程度，驱动首页「让分身了解你」入口
显隐（全 sufficient 隐藏）。认证 DependsJwtAuth（当前登录用户）；owner 身份由
user_id → hasn_humans.hasn_id 解析（与 owner_memory 同范式）。

URL：`/api/v1/hasn/app/owner/profile-coverage`（由 `app/hasn/api/router.py` 在 `/owner`
前缀下挂载，daemon 代理）。读时惰性 assess（owner_memory 版本领先时重判，否则快读）。
"""

import sqlalchemy as sa

from fastapi import APIRouter, Request

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_memory.schema.owner_profile_coverage import OwnerProfileCoverageResponse
from backend.app.hasn_memory.service.owner_profile_coverage_service import owner_profile_coverage_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


async def _resolve_owner_id(request: Request, db: CurrentSession) -> str:
    user_id = request.user.id
    owner = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner


@router.get(
    '/profile-coverage',
    summary='查看分身对本人 5 维度画像的了解完整度',
    dependencies=[DependsJwtAuth],
)
async def get_my_profile_coverage(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[OwnerProfileCoverageResponse]:
    owner_id = await _resolve_owner_id(request, db)
    coverage = await owner_profile_coverage_service.assess_if_stale(db, owner_id=owner_id)
    return response_base.success(data=OwnerProfileCoverageResponse(**coverage))
