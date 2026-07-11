"""入站门控抑制箱放行端点（Owner JWT）。

事实源：docs/hasn-node设计文档/05-安全与权限/06-入站消息门控与抑制箱(外部→Agent全门控).md（S3）

daemon 经 BackendGateway owner 通道调用此端点放行被门控的外部→Agent 消息：
  POST /api/v1/hasn/app/suppressed/{message_id}/release  body {reason?}
权威 reason 取云端已落库的 suppress_reason（不信客户端传值）。
放行统一语义 = **同意并添加联系人**（D6 三合一：accept 关联请求 + 建边 + 重投该 peer 全部暂存
消息）；旧 mode(once/persist) 已退役，入参保留仅为旧客户端兼容、不再分叉。
统一信封返回（ResponseModel + response_base.success）：daemon transport decode_ok_envelope 依赖之。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn.service.inbound_release import (
    list_suppressed_for_owner,
    release_suppressed,
)
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/suppressed',
    summary='列出主人名下被入站门控抑制的消息（Owner JWT，供 daemon 镜像桥拉取）',
    dependencies=[DependsJwtAuth],
)
async def list_suppressed_messages(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    owner_id = auth['hasn_id']
    items = await list_suppressed_for_owner(db, owner_id=owner_id)
    return response_base.success(data={'items': items})


class SuppressedReleaseRequest(BaseModel):
    """放行入参。reason 仅作客户端意图标记（实际以云端落库 suppress_reason 为准）。

    mode 已退役（放行=同意并添加联系人，只有一种语义）：入参保留仅兼容旧 daemon/webui，忽略其值。
    """

    reason: str | None = Field(default=None, description='门控理由（客户端标记，云端以落库值为准）')
    mode: str | None = Field(default=None, description='[已退役] 旧放行模式，忽略；放行统一为同意并添加联系人')


@router.post(
    '/suppressed/{message_id}/release',
    summary='放行一条被入站门控抑制的外部→Agent 消息（Owner JWT）',
    dependencies=[DependsJwtAuth],
)
async def release_suppressed_message(
    db: CurrentSessionTransaction,
    message_id: Annotated[int, Path(description='被抑制消息的 message_id')],
    request_body: SuppressedReleaseRequest,
    auth: Annotated[dict, Depends(hasn_auth)],
) -> ResponseModel:
    owner_id = auth['hasn_id']
    # mode 已退役（放行=同意并添加联系人三合一），不再透传
    result = await release_suppressed(
        db,
        owner_id=owner_id,
        message_id=message_id,
    )
    return response_base.success(data=result)
