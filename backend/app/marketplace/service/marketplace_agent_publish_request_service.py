"""Agent 市场发布幂等请求服务。"""

from __future__ import annotations

import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model import MarketplaceAgentPublishRequest
from backend.common.exception import errors


class MarketplaceAgentPublishRequestService:
    """把同一 Agent、资源类型与幂等键串行化并回放首次结果。"""

    @staticmethod
    async def lock_and_get(
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        resource_kind: str,
        idempotency_key: str,
    ) -> MarketplaceAgentPublishRequest | None:
        lock_key = json.dumps(
            [agent_hasn_id, resource_kind, idempotency_key],
            ensure_ascii=False,
            separators=(',', ':'),
        )
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': lock_key},
        )
        return await db.scalar(
            select(MarketplaceAgentPublishRequest)
            .where(
                MarketplaceAgentPublishRequest.agent_hasn_id == agent_hasn_id,
                MarketplaceAgentPublishRequest.resource_kind == resource_kind,
                MarketplaceAgentPublishRequest.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )

    @staticmethod
    def require_same_content(
        request: MarketplaceAgentPublishRequest,
        *,
        content_hash: str,
    ) -> None:
        if request.content_hash == content_hash:
            return
        raise errors.ConflictError(
            msg='同一 Idempotency-Key 已用于不同内容',
            data={
                'resource_id': request.resource_id,
                'version': request.version,
                'content_hash': request.content_hash,
            },
        )

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        agent_hasn_id: str,
        owner_hasn_id: str,
        resource_kind: str,
        idempotency_key: str,
        asset_uri: str,
        content_hash: str,
        file_hash: str,
        work_session_id: str | None,
    ) -> MarketplaceAgentPublishRequest:
        request = MarketplaceAgentPublishRequest(
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            resource_kind=resource_kind,
            idempotency_key=idempotency_key,
            asset_uri=asset_uri,
            content_hash=content_hash,
            file_hash=file_hash,
            state='processing',
            work_session_id=work_session_id,
        )
        db.add(request)
        await db.flush()
        return request

    @staticmethod
    async def save_result(
        db: AsyncSession,
        request: MarketplaceAgentPublishRequest,
        *,
        resource_id: str,
        version: str,
        state: str,
        result: dict,
    ) -> None:
        request.resource_id = resource_id
        request.version = version
        request.state = state
        request.result = result
        await db.flush()


marketplace_agent_publish_request_service = MarketplaceAgentPublishRequestService()
