"""通用语音模型签名目录 - 节点拉取面（Owner JWT）SPCAT-4。

daemon 持 Owner JWT，经 sync_agents 响应 speech_catalog_revision 检测变化后，拉取本端点取
{catalog_json（签名原文）, revision}，用内置公钥自行验签，写本地 catalog.json，据此判定各 STT
模型是否可安装。语音目录为全局配置（非 owner 私有），任一已登录 owner 读到的都是同一权威单行。
"""

from fastapi import APIRouter

from backend.app.hasn.schema.hasn_speech_catalog import SpeechCatalogNodeResponse
from backend.app.hasn.service.speech_catalog_service import speech_catalog_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/catalog',
    summary='拉取签名语音模型目录（节点下发：daemon 验签后判定可安装 STT 模型）',
    dependencies=[DependsJwtAuth],
    name='app_get_speech_catalog',
)
async def get_speech_catalog(db: CurrentSession) -> ResponseSchemaModel[SpeechCatalogNodeResponse]:
    data = await speech_catalog_service.get_node_response(db)
    return response_base.success(data=data)
