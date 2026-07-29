"""Growth → Publish 的公开表单权威解析内部接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.hasn_publish.service.publish_service import publish_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.internal_auth import require_publish_internal_token
from backend.database.db import CurrentSession

router = APIRouter()


class ResolveFormAccessRequest(BaseModel):
    """只接受公开引用与 Publish 签发令牌，不接受任何客户端项目 ID。"""

    publish_ref: str = Field(min_length=1, max_length=64)
    form_access_token: str = Field(min_length=1, max_length=4096)


class GrowthSiteStatusRequest(BaseModel):
    """Growth 服务端来源事实；仅内部服务可提交。"""

    owner_hasn_id: str = Field(min_length=1, max_length=64)
    platform_project_id: str = Field(min_length=1, max_length=64)
    growth_project_id: str = Field(min_length=1, max_length=64)


@router.post(
    '/forms/resolve',
    summary='解析公开表单的站点与项目权威绑定',
    dependencies=[Depends(require_publish_internal_token)],
)
async def resolve_form_access(
    db: CurrentSession,
    obj: ResolveFormAccessRequest,
) -> ResponseModel:
    data = await publish_service.resolve_form_access(
        db,
        publish_ref=obj.publish_ref,
        form_access_token=obj.form_access_token,
    )
    return response_base.success(data=data)


@router.post(
    '/growth/sites/status',
    summary='按 Growth 来源读取唯一站点状态',
    dependencies=[Depends(require_publish_internal_token)],
)
async def growth_site_status(
    db: CurrentSession,
    obj: GrowthSiteStatusRequest,
) -> ResponseModel:
    site = await publish_service.get_growth_site_status(
        db,
        owner_id=obj.owner_hasn_id,
        platform_project_id=obj.platform_project_id,
        growth_project_id=obj.growth_project_id,
    )
    return response_base.success(data={'site': site})
