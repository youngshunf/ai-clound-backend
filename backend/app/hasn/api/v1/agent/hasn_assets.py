"""HASN 资产 - Agent 端本地原件快照上传 API。

身份只取 Agent JWT；调用方不能指定 owner。原件仅在主人显式分享动作后由 daemon 调用本端点，
进入私有桶并按内容幂等登记。
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from backend.app.hasn.schema.asset_api import UploadedSourceSnapshot
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSessionTransaction

router = APIRouter()


@router.post('/upload', summary='上传本地原件快照（Agent JWT、私有桶、内容幂等）')
async def upload_local_source_snapshot(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    file: Annotated[UploadFile, File(description='主人已确认分享的原件快照')],
    width: Annotated[int | None, Form()] = None,
    height: Annotated[int | None, Form()] = None,
    duration_ms: Annotated[int | None, Form()] = None,
) -> ResponseSchemaModel[UploadedSourceSnapshot]:
    data = await file.read()
    asset = await hasn_asset_service.upload_local_source_snapshot(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        data=data,
        filename=file.filename,
        content_type=file.content_type,
        width=width,
        height=height,
        duration_ms=duration_ms,
    )
    if asset.content_sha256 is None:
        raise errors.ServerError(msg='本地原件快照上传后缺少内容哈希')
    return response_base.success(
        data=UploadedSourceSnapshot(
            asset_id=asset.asset_id,
            asset_uri=f'hasn://asset/{asset.asset_id}',
            kind=asset.kind,
            mime=asset.mime,
            size=asset.size_bytes,
            content_sha256=asset.content_sha256,
            width=asset.width,
            height=asset.height,
            duration_ms=asset.duration_ms,
        )
    )
