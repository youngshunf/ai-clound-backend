"""平台默认配置 - 节点拉取面（Owner JWT）。

daemon 持 Owner JWT，经 sync_agents 响应 platform_config_revision 检测变化后，
拉取本端点取全量 {config, revision}，写本地镜像并应用（media 覆盖层 + 活跃绑定 re-provision）。
平台默认为全局配置（非 owner 私有），任一已登录 owner 读到的都是同一权威单行。
"""

from fastapi import APIRouter

from backend.app.hasn.schema.hasn_platform_default_config import PlatformDefaultConfigResponse
from backend.app.hasn.service.model_registry_downlink_service import model_registry_downlink_service
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
    '/models',
    summary='拉取模型注册表（按能力类别分组下发：语义 + 输入要求 + 价格档位）',
    dependencies=[DependsJwtAuth],
    name='app_get_model_registry_downlink',
)
async def get_model_registry_downlink(db: CurrentSession) -> ResponseModel:
    """daemon 拉本目录写本地镜像，据此过滤候选、校验输入、暴露给分身自主选型。

    只下发已标注（`capability != unclassified`）、网关上还在（`upstream_status = active`）、
    且运营显式放开（`agent_visible`）的模型；**不含原始计费倍率**（内部计费口径不外泄）。

    **不并进 `/platform/config`** 是有意的：PDC 的 revision 由 config_json 算，而注册表内容
    不在其中——并进去会「内容变了 revision 没变」，daemon 缓存永远刷不新。故本端点自带
    `registry_revision`，daemon 按它判断是否重拉。
    """
    return response_base.success(data=await model_registry_downlink_service.list_downlink(db))


@router.get(
    '/video-models',
    summary='[deprecated] 拉取视频模型目录（已被 /platform/models 取代，P4 移除）',
    dependencies=[DependsJwtAuth],
    name='app_get_video_model_catalog',
    deprecated=True,
)
async def get_video_model_catalog(db: CurrentSession) -> ResponseModel:
    """**已废弃**：语义来源从 PDC 的 `node.media.video_models` 迁到模型注册表，请改用
    `GET /platform/models`（按能力类别分组，视频在 `models.video` 下）。

    保留一版是给还没升级的 daemon 兜底——旧 daemon 拿不到新端点会整个失去视频模型清单。
    P4 存量迁移完成后移除。
    """
    models = await video_model_catalog_service.list_catalog(db)
    return response_base.success(data={'models': models})
