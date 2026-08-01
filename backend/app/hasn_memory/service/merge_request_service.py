"""合并待办服务（doc19 §5.5：主脑离线时的每 owner 待办）。

非主脑分身整理完自己那片后请求合并；主脑离线则落云端待办。**去重只留最新一条，不堆积**——
`owner_id` 是主键，`request_merge` 走覆盖式 upsert，不排队。主脑上线消化后写 `consumed_time`。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_memory.crud.crud_merge_request import merge_request_dao
from backend.app.hasn_memory.model import MergeRequest
from backend.app.hasn_memory.schema.merge_request import CreateMergeRequestParam


class MergeRequestService:
    """合并待办云端权威读写。"""

    @staticmethod
    async def request_merge(
        db: AsyncSession,
        *,
        owner_id: str,
        requested_by_agent: str,
        requested_by_node: str,
        reason: str | None = None,
    ) -> MergeRequest:
        """登记（或覆盖）该主人的合并待办；同主人重复请求只保留最新一条。"""
        if not owner_id or not requested_by_agent or not requested_by_node:
            raise ValueError('owner_id / requested_by_agent / requested_by_node 均必填')
        return await merge_request_dao.upsert(
            db,
            CreateMergeRequestParam(
                owner_id=owner_id,
                requested_by_agent=requested_by_agent,
                requested_by_node=requested_by_node,
                reason=reason,
            ),
        )

    @staticmethod
    async def get_pending(db: AsyncSession, owner_id: str) -> MergeRequest | None:
        """取尚未消化的待办（None = 无待办）。滞留时长由调用方按 `requested_time` 算。"""
        return await merge_request_dao.get_pending(db, owner_id)

    @staticmethod
    async def consume(db: AsyncSession, owner_id: str) -> bool:
        """主脑消化待办：返回 True 表示确实有一条待办被本次消化掉。"""
        return await merge_request_dao.mark_consumed(db, owner_id)


merge_request_service: MergeRequestService = MergeRequestService()
