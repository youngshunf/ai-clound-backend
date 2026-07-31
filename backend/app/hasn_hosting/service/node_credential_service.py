"""节点面凭据服务（H2 · 契约 §3.2）。

容器内 daemon 首启时**没有任何身份**，只有一把 tmpfs 里的授权码。本服务把它换成该容器自己的
设备级 access/refresh token（与主人 token 是不同主体，可被单独吊销）。

限流是必须的：这个端点无 Owner JWT，任何人都能打。按 IP + code_hash 双维度计数，触限直接拒，
且失败响应除三个既定错误码外不泄露任何信息（不告诉调用方码属于谁、绑哪台设备）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.app.hasn_hosting.constants import (
    CODE_EXCHANGE_RATE_LIMIT_PER_CODE,
    CODE_EXCHANGE_RATE_LIMIT_PER_IP,
    CODE_EXCHANGE_RATE_LIMIT_PREFIX,
    CODE_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS,
    ERR_RATE_LIMITED,
    NODE_STATUS_STARTING,
)
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.service.authorization_code_service import (
    ExchangeFailure,
    authorization_code_service,
    hash_code,
)
from backend.common.log import log
from backend.common.security.jwt import create_access_token, create_refresh_token
from backend.core.conf import settings
from backend.database.redis import redis_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ExchangedCredential:
    """兑换成功的设备级凭据（只回给容器内 daemon）。"""

    access_token: str
    refresh_token: str
    expires_in: int
    user_id: int
    owner_hasn_id: str
    node_id: str
    session_uuid: str


class NodeCredentialService:
    """授权码 → 设备级 token。"""

    @staticmethod
    async def _hit_rate_limit(*, client_ip: str, code_hash: str) -> bool:
        """按 IP + code_hash 双维度计数；任一维度超阈值即触限。"""
        limits = (
            (f'{CODE_EXCHANGE_RATE_LIMIT_PREFIX}:ip:{client_ip}', CODE_EXCHANGE_RATE_LIMIT_PER_IP),
            (f'{CODE_EXCHANGE_RATE_LIMIT_PREFIX}:code:{code_hash}', CODE_EXCHANGE_RATE_LIMIT_PER_CODE),
        )
        limited = False
        for key, threshold in limits:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, CODE_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS)
            if count > threshold:
                limited = True
        return limited

    async def exchange(
        self,
        db: AsyncSession,
        *,
        code: str,
        node_id: str,
        client_ip: str,
    ) -> tuple[ExchangedCredential | None, ExchangeFailure | None]:
        """兑换授权码（§3.2）。成功后把新 session 的 `session_uuid` 写进托管行，供单独吊销。"""
        code_hash = hash_code(code)
        if await self._hit_rate_limit(client_ip=client_ip, code_hash=code_hash):
            # 429 类拒绝，可重试 → warn
            log.warning(f'[hosting] 授权码兑换触发限流: ip={client_ip}, node_id={node_id}')
            return None, ExchangeFailure(error=ERR_RATE_LIMITED, message='兑换过于频繁，请稍后再试')

        row, failure = await authorization_code_service.consume(db, plain_code=code, node_id=node_id)
        if row is None:
            assert failure is not None
            log.warning(f'[hosting] 授权码兑换失败: node_id={node_id}, error={failure.error}')
            return None, failure

        # 设备凭据必须 multi_login=True：False 会按前缀清掉该用户**所有**设备的 token，
        # 等于容器一上线就把主人的桌面端全部登出。
        access = await create_access_token(row.user_id, multi_login=True, node_id=node_id, node_type='cloud')
        refresh = await create_refresh_token(access.session_uuid, row.user_id, multi_login=True)

        cloud_node = (
            await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
        ).scalar_one_or_none()
        if cloud_node is not None:
            cloud_node.credential_session_uuid = access.session_uuid
            if cloud_node.status not in ('online', 'updating'):
                cloud_node.status = NODE_STATUS_STARTING
            cloud_node.failure_reason = None
            cloud_node.failure_detail = None
        else:
            # 授权码存在但托管行不见了属不变量破坏（两者在同一事务里创建）
            log.error(f'[hosting] 授权码兑换成功但托管行缺失: node_id={node_id}')
        await db.flush()

        return (
            ExchangedCredential(
                access_token=access.access_token,
                refresh_token=refresh.refresh_token,
                expires_in=int(settings.TOKEN_EXPIRE_SECONDS),
                user_id=row.user_id,
                owner_hasn_id=row.owner_hasn_id,
                node_id=node_id,
                session_uuid=access.session_uuid,
            ),
            None,
        )


node_credential_service = NodeCredentialService()
