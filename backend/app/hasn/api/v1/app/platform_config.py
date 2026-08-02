"""平台默认配置 - 节点拉取面（Owner JWT）。

daemon 持 Owner JWT，经 sync_agents 响应 platform_config_revision 检测变化后，
拉取本端点取全量 {config, revision}，写本地镜像并应用（media 覆盖层 + 活跃绑定 re-provision）。
平台默认为全局配置（非 owner 私有），任一已登录 owner 读到的都是同一权威单行。
"""

from fastapi import APIRouter

from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfigResponse
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.app.hasn.service.video_model_catalog_service import video_model_catalog_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/config',
    summary='拉取平台默认配置（节点下发：媒体模型 + agent 运行时四槽默认）',
    dependencies=[DependsJwtAuth],
    name='app_get_platform_default_config',
)
async def get_platform_default_config(db: CurrentSession) -> ResponseSchemaModel[PlatformDefaultConfigResponse]:
    data = await platform_default_config_service.get_response(db)
    return response_base.success(data=data)


@router.get(
    '/video-models',
    summary='拉取视频模型目录（new-api 可用性/定价 × PDC 语义元数据）',
    dependencies=[DependsJwtAuth],
    name='app_get_video_model_catalog',
)
async def get_video_model_catalog(db: CurrentSession) -> ResponseModel:
    """daemon 拉本目录后暴露给分身，供其按任务复杂度自主选型。

    **不并进 `/platform/config`** 是有意的：PDC 的 revision 由 config_json 算，而可用性与定价
    来自 new-api、不在 config_json 里——并进去会出现「内容变了 revision 没变」，daemon 的缓存
    永远刷不新。故单独端点、由 daemon 自己按 TTL 刷新。
    """
    models = await video_model_catalog_service.list_catalog(db)
    return response_base.success(data={'models': models})
